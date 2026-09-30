"""Real-time detection on canonical events: critical rules + early-warning trends.

Per vehicle the detector keeps O(1) streaming state (features.py) and a small
rule state machine with hysteresis: a rule OPENS when its open condition holds
and CLEARS only when a stricter clear condition holds, so noisy signals near a
threshold do not flap. Every transition is published on `alerts`.

Alert fingerprint = BLAKE2b(vehicle_id, rule, seq of the opening event): the
same input replayed yields the same fingerprint, so the PostgreSQL sink (M6)
can upsert idempotently.

Signals and why they lead failures (see prognos_sim.fleet for the physics):
  COOLANT_DRIFT      CUSUM of coolant above the warm-engine expectation
  MISFIRE_ROUGHNESS  EW std of rpm around what speed implies
  LV_BATTERY_WEAK    EWMA of resting (ignition off) 12 V voltage
  TYRE_SLOW_LEAK     tyre deficit vs the median of the other three (cancels
                     temperature/load), with EW trend -> hours to critical
  HV_CELL_IMBALANCE  EWMA of cell voltage spread
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import blake2b
from typing import Any

import orjson

from prognos_common.catalog import DTC_BY_CODE, Severity
from prognos_stream.features import Cusum, Ewma, EwTrend, EwVar

WARM_ENGINE_S = 900.0  # coolant expectation only valid after 15 min with ignition on
DTC_WINDOW_S = 600.0
DTC_WARNING_COUNT = 3  # warning DTCs must repeat within the window (noise filter)
STATE_INTERVAL_S = 15.0


@dataclass(frozen=True, slots=True)
class Rule:
    code: str
    failure_mode: str | None
    severity: str
    opens: Callable[[float], bool]
    clears: Callable[[float], bool]
    threshold: float


RULES: dict[str, Rule] = {
    r.code: r
    for r in (
        # Critical, immediate (target < 5 s from event to alert).
        Rule("COOLANT_OVERHEAT", "COOLING_FAILURE", "critical",
             lambda v: v > 112.0, lambda v: v < 105.0, 112.0),
        Rule("TYRE_PRESSURE_CRITICAL", "TYRE_SLOW_LEAK", "critical",
             lambda v: v > 0.25, lambda v: v < 0.15, 0.25),
        Rule("HV_CELL_CRITICAL", "HV_BATTERY_THERMAL", "critical",
             lambda v: v > 100.0, lambda v: v < 60.0, 100.0),
        Rule("LV_BATTERY_CRITICAL", "LV_BATTERY_FAILURE", "critical",
             lambda v: v < 11.8, lambda v: v > 12.2, 11.8),
        Rule("VEHICLE_BREAKDOWN", None, "critical",
             lambda v: v > 0.5, lambda v: v < 0.5, 1.0),
        # Early warnings from trends (the predictive signal).
        Rule("COOLANT_DRIFT", "COOLING_FAILURE", "warning",
             lambda v: v > 60.0, lambda v: v < 5.0, 60.0),
        Rule("MISFIRE_ROUGHNESS", "IGNITION_MISFIRE", "warning",
             lambda v: v > 90.0, lambda v: v < 70.0, 90.0),
        Rule("LV_BATTERY_WEAK", "LV_BATTERY_FAILURE", "warning",
             lambda v: v < 12.25, lambda v: v > 12.45, 12.25),
        Rule("TYRE_SLOW_LEAK", "TYRE_SLOW_LEAK", "warning",
             lambda v: v > 0.08, lambda v: v < 0.05, 0.08),
        Rule("HV_CELL_IMBALANCE", "HV_BATTERY_THERMAL", "warning",
             lambda v: v > 40.0, lambda v: v < 25.0, 40.0),
    )
}  # fmt: skip


def fingerprint(vehicle_id: str, rule: str, seq: int) -> str:
    return blake2b(f"{vehicle_id}:{rule}:{seq}".encode(), digest_size=16).hexdigest()


def _iso(ts: float) -> str:
    sec = int(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec)) + f".{int((ts - sec) * 1000):03d}Z"


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()  # canonical ts is ISO-8601 UTC ("...Z")


@dataclass(slots=True)
class Episode:
    fingerprint: str
    opened_ts: float
    value: float


@dataclass(slots=True)
class VehicleState:
    tenant_id: str
    vin: str
    last_ts: float = 0.0
    ignition_since: float | None = None
    coolant_cusum: Cusum = field(default_factory=lambda: Cusum(k=4.0))
    rpm_residual: EwVar = field(default_factory=lambda: EwVar(alpha=0.02))
    lv_rest: Ewma = field(default_factory=lambda: Ewma(alpha=0.05))
    cell_delta: Ewma = field(default_factory=lambda: Ewma(alpha=0.05))
    tyre_deficit: list[Ewma] = field(default_factory=lambda: [Ewma(0.05) for _ in range(4)])
    tyre_trend: list[EwTrend] = field(default_factory=lambda: [EwTrend(3600.0) for _ in range(4)])
    dtc_events: deque[tuple[float, str]] = field(default_factory=deque)
    open_rules: dict[str, Episode] = field(default_factory=dict)
    last_state_ts: float = 0.0
    last_event: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Output:
    topic: str  # "alerts" | "vehicle.state"
    key: bytes
    value: bytes


class Detector:
    def __init__(self, *, state_interval_s: float = STATE_INTERVAL_S) -> None:
        self.partitions: dict[int, dict[str, VehicleState]] = {}
        self.state_interval_s = state_interval_s
        self.counts: dict[str, int] = {}
        self.alert_latencies: list[float] = []  # detected_at - event_ts, for opened alerts

    def forget_partition(self, partition: int) -> None:
        self.partitions.pop(partition, None)

    def vehicles_tracked(self) -> int:
        return sum(len(p) for p in self.partitions.values())

    def _count(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    # ------------------------------------------------------------------ main entry
    def process(self, event: dict[str, Any], partition: int, now: float) -> list[Output]:
        vehicles = self.partitions.setdefault(partition, {})
        vid = event["vehicle_id"]
        state = vehicles.get(vid)
        if state is None:
            state = vehicles[vid] = VehicleState(event["tenant_id"], event["vin"])
        ts = _epoch(event["event_ts"])
        in_order = ts >= state.last_ts
        signals = self._signals(state, event, ts, in_order)
        outputs: list[Output] = []
        for code, value in signals.items():
            outputs.extend(self._evaluate(state, vid, RULES[code], value, event, ts, now))
        outputs.extend(self._dtc_rules(state, vid, event, ts, now))
        if in_order:
            state.last_ts = ts
            state.last_event = event
            if ts - state.last_state_ts >= self.state_interval_s:
                state.last_state_ts = ts
                outputs.append(self._snapshot(state, vid, ts, signals))
        self._count("events")
        return outputs

    # ------------------------------------------------------------------ signals
    def _signals(
        self, s: VehicleState, e: dict[str, Any], ts: float, in_order: bool
    ) -> dict[str, float]:
        """Update streaming state (in-order events only) and return rule inputs."""
        out: dict[str, float] = {}
        ignition = bool(e.get("ignition_on"))
        speed = e["speed_kmh"]
        coolant = e.get("coolant_temp_c")
        if coolant is not None:
            out["COOLANT_OVERHEAT"] = coolant
        out["VEHICLE_BREAKDOWN"] = 1.0 if e["event_type"] == "BREAKDOWN" else (
            1.0 if "VEHICLE_BREAKDOWN" in s.open_rules and speed < 1.0 else 0.0
        )  # fmt: skip

        if in_order:
            if ignition and s.ignition_since is None:
                s.ignition_since = ts
            elif not ignition:
                s.ignition_since = None
            warm = s.ignition_since is not None and ts - s.ignition_since >= WARM_ENGINE_S
            if coolant is not None and warm:
                out["COOLANT_DRIFT"] = s.coolant_cusum.update(coolant - (88.0 + 0.03 * speed))
            rpm = e.get("engine_rpm")
            if rpm and ignition and speed > 5.0:
                s.rpm_residual.update(rpm - (750.0 + 28.0 * speed))
                if s.rpm_residual.n >= 60:
                    out["MISFIRE_ROUGHNESS"] = s.rpm_residual.std
            lv = e.get("lv_battery_v")
            if lv is not None and not ignition:
                s.lv_rest.update(lv)
                if s.lv_rest.n >= 30:
                    out["LV_BATTERY_WEAK"] = s.lv_rest.value
                    out["LV_BATTERY_CRITICAL"] = s.lv_rest.value
            cell = e.get("hv_cell_delta_mv")
            if cell is not None:
                s.cell_delta.update(cell)
                if s.cell_delta.n >= 30:
                    out["HV_CELL_IMBALANCE"] = s.cell_delta.value
                    out["HV_CELL_CRITICAL"] = s.cell_delta.value
            tyres = [e.get("tyre_fl_kpa"), e.get("tyre_fr_kpa"), e.get("tyre_rl_kpa"),
                     e.get("tyre_rr_kpa")]  # fmt: skip
            if all(t is not None for t in tyres):
                worst = 0.0
                for i, pressure in enumerate(tyres):
                    others = sorted(tyres[:i] + tyres[i + 1 :])  # type: ignore[type-var]
                    reference = others[1]  # median of the other three
                    ratio = max(0.0, (reference - pressure) / reference) if reference else 0.0
                    smoothed = s.tyre_deficit[i].update(ratio)
                    s.tyre_trend[i].update(ts, ratio)
                    worst = max(worst, smoothed)
                if s.tyre_deficit[0].n >= 30:
                    out["TYRE_SLOW_LEAK"] = worst
                    out["TYRE_PRESSURE_CRITICAL"] = worst
        return out

    # ------------------------------------------------------------------ rules
    def _evaluate(
        self,
        s: VehicleState,
        vid: str,
        rule: Rule,
        value: float,
        e: dict[str, Any],
        ts: float,
        now: float,
    ) -> list[Output]:
        episode = s.open_rules.get(rule.code)
        if episode is None and rule.opens(value):
            fp = fingerprint(vid, rule.code, e["seq"])
            s.open_rules[rule.code] = Episode(fp, ts, value)
            details = self._details(s, rule.code, value)
            return [self._alert(s, vid, rule.code, rule.severity, rule.failure_mode, "open",
                                fp, value, rule.threshold, e, ts, now, details)]  # fmt: skip
        if episode is not None and rule.clears(value):
            del s.open_rules[rule.code]
            fp = episode.fingerprint
            return [self._alert(s, vid, rule.code, rule.severity, rule.failure_mode, "cleared",
                                fp, value, rule.threshold, e, ts, now, {})]  # fmt: skip
        return []

    def _dtc_rules(
        self, s: VehicleState, vid: str, e: dict[str, Any], ts: float, now: float
    ) -> list[Output]:
        outputs: list[Output] = []
        window = s.dtc_events
        for code in e.get("dtc_codes") or ():
            window.append((ts, code))
        while window and window[0][0] < ts - DTC_WINDOW_S:
            window.popleft()
        counts: dict[str, int] = {}
        for _, code in window:
            counts[code] = counts.get(code, 0) + 1
        for code, n in counts.items():
            definition = DTC_BY_CODE.get(code)
            if definition is None or definition.severity is Severity.INFO:
                continue
            rule_code = f"DTC_{code}"
            critical = definition.severity is Severity.CRITICAL
            if rule_code not in s.open_rules and (critical or n >= DTC_WARNING_COUNT):
                fp = fingerprint(vid, rule_code, e["seq"])
                s.open_rules[rule_code] = Episode(fp, ts, float(n))
                outputs.append(
                    self._alert(s, vid, rule_code, definition.severity.value,
                                definition.failure_mode, "open", fp, float(n),
                                1.0 if critical else float(DTC_WARNING_COUNT), e, ts, now,
                                {"description": definition.description, "occurrences": n})
                )  # fmt: skip
        for rule_code in [r for r in s.open_rules if r.startswith("DTC_")]:
            if rule_code.removeprefix("DTC_") not in counts:
                episode = s.open_rules.pop(rule_code)
                definition = DTC_BY_CODE[rule_code.removeprefix("DTC_")]
                outputs.append(
                    self._alert(s, vid, rule_code, definition.severity.value,
                                definition.failure_mode, "cleared", episode.fingerprint, 0.0,
                                0.0, e, ts, now, {})
                )  # fmt: skip
        return outputs

    def _details(self, s: VehicleState, code: str, value: float) -> dict[str, Any]:
        if code != "TYRE_SLOW_LEAK":
            return {}
        worst = max(range(4), key=lambda i: s.tyre_deficit[i].value)
        slope = s.tyre_trend[worst].slope_per_hour  # deficit ratio per hour (positive = worsening)
        hours = (0.25 - value) / slope if slope > 1e-6 else None
        return {
            "tyre": ("front_left", "front_right", "rear_left", "rear_right")[worst],
            "deficit_pct": round(value * 100, 1),
            "leak_rate_pct_per_hour": round(slope * 100, 2),
            "estimated_hours_to_critical": None if hours is None else round(max(hours, 0.0), 1),
        }

    def _alert(
        self,
        s: VehicleState,
        vid: str,
        rule_code: str,
        severity: str,
        failure_mode: str | None,
        status: str,
        fp: str,
        value: float,
        threshold: float,
        e: dict[str, Any],
        ts: float,
        now: float,
        details: dict[str, Any],
    ) -> Output:
        body = {
            "schema_version": 1,
            "fingerprint": fp,
            "status": status,
            "tenant_id": s.tenant_id,
            "vehicle_id": vid,
            "vin": s.vin,
            "rule_code": rule_code,
            "severity": severity,
            "failure_mode": failure_mode,
            "event_ts": e["event_ts"],
            "event_seq": e["seq"],
            "detected_at": _iso(now),
            "value": round(value, 3),
            "threshold": threshold,
            "details": details,
        }
        self._count(f"alerts_{status}")
        if status == "open":
            self.alert_latencies.append(now - ts)
            if len(self.alert_latencies) > 100_000:
                del self.alert_latencies[:50_000]
        return Output("alerts", vid.encode(), orjson.dumps(body))

    def _snapshot(self, s: VehicleState, vid: str, ts: float, signals: dict[str, float]) -> Output:
        e = s.last_event
        penalties = {"critical": 40, "warning": 15}
        health = 100
        for code in s.open_rules:
            rule = RULES.get(code)
            severity = rule.severity if rule else (
                DTC_BY_CODE[code.removeprefix("DTC_")].severity.value
            )  # fmt: skip
            health -= penalties.get(severity, 0)
        body = {
            "vehicle_id": vid,
            "tenant_id": s.tenant_id,
            "vin": s.vin,
            "as_of": _iso(ts),
            "latitude": e.get("latitude"),
            "longitude": e.get("longitude"),
            "speed_kmh": e.get("speed_kmh"),
            "ignition_on": e.get("ignition_on"),
            "fuel_level_pct": e.get("fuel_level_pct"),
            "hv_soc_pct": e.get("hv_soc_pct"),
            "health_score": max(health, 0),
            "active_alerts": sorted(s.open_rules),
            "indicators": {k: round(v, 3) for k, v in signals.items()},
        }
        return Output("vehicle.state", vid.encode(), orjson.dumps(body))

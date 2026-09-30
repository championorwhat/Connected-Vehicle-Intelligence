"""Offline back-test: simulator -> normalizer -> detector, scored against ground truth.

    uv run python -m prognos_stream.evaluate --vehicles 300 --hours 8 \\
        --fault-rate 0.2 --time-scale 48 --output evidence/benchmarks/m5-detection-backtest.json

Runs the real components in one process on a simulated clock (no Kafka), so a
multi-hour, multi-fault scenario takes minutes and is exactly reproducible.

Scoring:
  * recall      share of ground-truth FAILUREs preceded by an alert with the same
                failure mode for that vehicle, raised after the fault's onset
  * lead time   failure_ts - first such alert (how early the manager is warned)
  * precision   share of opened failure-mode alerts that fall inside a real fault
                episode of that mode on that vehicle (onset .. failure + downtime)
`--time-scale` compresses degradation (days -> hours) so faults complete within
the run; driving behaviour is NOT compressed, which makes ignition-dependent
signals (resting battery voltage, warm-engine coolant) harder to observe than in
real time. Results are therefore a conservative baseline for M7/M8, not a claim
about field performance.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson

from prognos_common.catalog import FAILURE_MODES
from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.fleet import DOWNTIME_HOURS
from prognos_stream.detector import Detector
from prognos_stream.processor import CANONICAL, Normalizer
from prognos_stream.registry import VehicleRegistry

START = 1_790_000_000.0


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


@dataclass
class PipelinePublisher:
    """A publisher that feeds each raw message straight through normalizer and detector."""

    normalizer: Normalizer
    detector: Detector
    clock: list[float]
    truth: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    raw: int = 0

    def publish(self, topic: str, key: bytes, value: bytes, headers: list[Any]) -> None:
        if topic == "sim.truth":
            self.truth.append(orjson.loads(value))
            return
        self.raw += 1
        hdrs = {k: v for k, v in headers if isinstance(v, bytes)}
        now = self.clock[0]
        outcome = self.normalizer.process(value, hdrs, 0, now)
        if outcome.kind != CANONICAL:
            return
        for out in self.detector.process(orjson.loads(outcome.value), 0, now):
            if out.topic == "alerts":
                self.alerts.append(orjson.loads(out.value))

    def poll(self) -> None:
        return None

    def flush(self, timeout: float = 30.0) -> int:
        return 0

    def stats(self) -> dict[str, int]:
        return {"published": self.raw}


def score(
    truth: list[dict[str, Any]], alerts: list[dict[str, Any]], scale: float
) -> dict[str, Any]:
    downtime_s = DOWNTIME_HOURS[1] * 3600.0 / scale
    episodes: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    failures = []
    for record in truth:
        key = (record["vehicle_id"], record["failure_mode"])
        onset, failure = _ts(record["onset_ts"]), _ts(record["failure_ts"])
        if record["type"] == "FAULT_ONSET":
            episodes[key].append((onset, failure + downtime_s))
        else:
            failures.append((key, onset, failure))

    opened = [a for a in alerts if a["status"] == "open" and a["failure_mode"]]
    first_alert: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    per_rule: dict[str, dict[str, int]] = defaultdict(lambda: {"opened": 0, "true": 0})
    for alert in opened:
        key = (alert["vehicle_id"], alert["failure_mode"])
        ts = _ts(alert["event_ts"])
        first_alert[key].append((ts, alert["rule_code"]))
        per_rule[alert["rule_code"]]["opened"] += 1
        if any(start <= ts <= end for start, end in episodes.get(key, ())):
            per_rule[alert["rule_code"]]["true"] += 1

    per_mode: dict[str, dict[str, Any]] = {
        fm.code: {"failures": 0, "warned": 0, "lead_times_s": []} for fm in FAILURE_MODES
    }
    for key, onset, failure in failures:
        stats = per_mode[key[1]]
        stats["failures"] += 1
        before = [ts for ts, _ in first_alert.get(key, ()) if onset <= ts < failure]
        if before:
            stats["warned"] += 1
            stats["lead_times_s"].append(failure - min(before))

    def summarise(leads: list[float]) -> dict[str, float] | None:
        if not leads:
            return None
        return {
            "median_hours_real_time": round(statistics.median(leads) * scale / 3600, 1),
            "min_hours_real_time": round(min(leads) * scale / 3600, 1),
        }

    total_failures = sum(s["failures"] for s in per_mode.values())
    total_warned = sum(s["warned"] for s in per_mode.values())
    all_leads = [lt for s in per_mode.values() for lt in s["lead_times_s"]]
    true_alerts = sum(r["true"] for r in per_rule.values())
    return {
        "failures": total_failures,
        "failures_warned_before_breakdown": total_warned,
        "recall": round(total_warned / total_failures, 3) if total_failures else None,
        "lead_time": summarise(all_leads),
        "alerts_opened_with_failure_mode": len(opened),
        "precision": round(true_alerts / len(opened), 3) if opened else None,
        "per_failure_mode": {
            mode: {
                "failures": s["failures"],
                "warned": s["warned"],
                "recall": round(s["warned"] / s["failures"], 3) if s["failures"] else None,
                "lead_time": summarise(s["lead_times_s"]),
            }
            for mode, s in per_mode.items()
        },
        "per_rule": {
            rule: {**r, "precision": round(r["true"] / r["opened"], 3)}
            for rule, r in sorted(per_rule.items())
        },
    }


def run(vehicles: int, hours: float, fault_rate: float, scale: float, seed: int) -> dict[str, Any]:
    roster = generate_roster(vehicles, max(1, min(20, vehicles // 20)), seed)
    cfg = SimConfig(
        vehicle_count=vehicles, events_per_second=float(vehicles), tenant_count=len(roster.tenants),
        seed=seed, tick_hz=1, mode=Mode.FAST, publisher=PublisherKind.NULL, metrics_port=0,
        fault_rate=fault_rate, fault_time_scale=scale, burst_multiplier=1.0,
    )  # fmt: skip
    clock = [START]
    pipe = PipelinePublisher(Normalizer(VehicleRegistry.from_roster(roster)), Detector(), clock)
    sim = ShardSimulator(cfg, 0, roster.vehicles, pipe, START)
    wall = time.perf_counter()
    for tick in range(int(hours * 3600)):
        clock[0] = START + tick
        sim.tick(clock[0], 1.0)
    sim.finish()
    elapsed = time.perf_counter() - wall
    result = score(pipe.truth, pipe.alerts, scale)
    result["run"] = {
        "vehicles": vehicles, "simulated_hours": hours, "fault_rate": fault_rate,
        "fault_time_scale": scale, "seed": seed, "raw_messages": pipe.raw,
        "wall_seconds": round(elapsed, 1),
        "pipeline_us_per_message": round(elapsed / max(pipe.raw, 1) * 1e6, 1),
        "alerts_opened": sum(1 for a in pipe.alerts if a["status"] == "open"),
        "alerts_cleared": sum(1 for a in pipe.alerts if a["status"] == "cleared"),
    }  # fmt: skip
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detection back-test against ground truth")
    parser.add_argument("--vehicles", type=int, default=300)
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--fault-rate", type=float, default=0.2)
    parser.add_argument("--time-scale", type=float, default=48.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run(args.vehicles, args.hours, args.fault_rate, args.time_scale, args.seed)
    result["environment"] = {"machine": platform.machine(), "cpu_count": os.cpu_count()}
    result["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

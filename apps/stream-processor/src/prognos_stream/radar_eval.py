"""Radar back-test: does it find a firmware defect, and stay quiet without one?

    uv run python -m prognos_stream.radar_eval --vehicles 3000 --hours 3 \
        --output evidence/benchmarks/m7-radar-backtest.json

Runs simulator -> normalizer -> radar in one process (fast mode, simulated time)
twice with the same seed: once with the `firmware_defect` scenario and once
without (control). Reports every signal from both runs.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_stream.processor import CANONICAL, Normalizer
from prognos_stream.radar import Radar, RadarConfig
from prognos_stream.radar_main import cohorts_from_roster, event_ts
from prognos_stream.registry import VehicleRegistry

T0 = 1_790_000_000.0


@dataclass
class RadarPublisher:
    normalizer: Normalizer
    radar: Radar
    clock: list[float]
    signals: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, topic: str, key: bytes, value: bytes, headers: list[Any]) -> None:
        if topic != "telemetry.raw":
            return
        hdrs = {k: v for k, v in headers if isinstance(v, bytes)}
        outcome = self.normalizer.process(value, hdrs, 0, self.clock[0])
        if outcome.kind != CANONICAL:
            return
        e = orjson.loads(outcome.value)
        self.signals += self.radar.add(e["vehicle_id"], event_ts(e["event_ts"]), e["dtc_codes"])

    def poll(self) -> None:
        return None

    def flush(self, timeout: float = 30.0) -> int:
        return 0

    def stats(self) -> dict[str, int]:
        return {}


def run(
    vehicles: int,
    hours: float,
    scenario: str,
    seed: int,
    radar_cfg: RadarConfig,
    defect_rate: float = 0.002,
) -> dict[str, Any]:
    roster = generate_roster(vehicles, max(1, min(20, vehicles // 50)), seed)
    cfg = SimConfig(
        vehicle_count=vehicles, events_per_second=vehicles / 10.0, tenant_count=1,
        seed=seed, tick_hz=1, mode=Mode.FAST, publisher=PublisherKind.NULL, metrics_port=0,
        burst_multiplier=1.0, fault_rate=0.01, scenario=scenario, defect_rate=defect_rate,
    )  # fmt: skip
    clock = [T0]
    radar = Radar(cohorts_from_roster(roster), radar_cfg)
    pipe = RadarPublisher(Normalizer(VehicleRegistry.from_roster(roster)), radar, clock)
    sim = ShardSimulator(cfg, 0, roster.vehicles, pipe, T0)
    started = time.perf_counter()
    for tick in range(int(hours * 3600)):
        clock[0] = T0 + tick
        sim.tick(clock[0], 1.0)
    sim.finish()
    pipe.signals += radar.advance(clock[0] + radar_cfg.window_s + radar_cfg.allowed_lateness_s)
    defect_vehicles = sum(
        1 for v in roster.vehicles
        if v.model_code == cfg.defect_model and v.firmware_version == cfg.defect_firmware
    )  # fmt: skip
    return {
        "scenario": scenario,
        "defect_cohort": {
            "model_code": cfg.defect_model,
            "firmware": cfg.defect_firmware,
            "dtc": cfg.defect_dtc,
            "vehicles": defect_vehicles,
            "per_event_rate": cfg.defect_rate,
        },
        "counts": radar.counts,
        "sim_counts": dict(sim.counts),
        "signals": pipe.signals,
        "wall_seconds": round(time.perf_counter() - started, 1),
    }


def score(defect: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    target = defect["defect_cohort"]

    def is_target(s: dict[str, Any]) -> bool:
        return (
            s["dtc"] == target["dtc"]
            and s["model_code"] == target["model_code"]
            and (s["firmware_version"] in (None, target["firmware"]))
        )

    hits = [s for s in defect["signals"] if s["level"] == "firmware" and is_target(s)]
    return {
        "defect_detected_at_firmware_level": bool(hits),
        "first_detection_window_end_hours": (
            round((min(s["window_end"] for s in hits) - T0) / 3600, 2) if hits else None
        ),
        "other_signals_in_defect_run": sum(1 for s in defect["signals"] if not is_target(s)),
        "signals_in_control_run": len(control["signals"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vehicles", type=int, default=3000)
    parser.add_argument("--hours", type=float, default=3.0)
    parser.add_argument("--window-seconds", type=float, default=3600.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    radar_cfg = RadarConfig(window_s=args.window_seconds)
    defect = run(args.vehicles, args.hours, "firmware_defect", args.seed, radar_cfg)
    control = run(args.vehicles, args.hours, "none", args.seed, radar_cfg)
    result = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {
            "vehicles": args.vehicles,
            "sim_hours": args.hours,
            "seed": args.seed,
            "radar": radar_cfg.__dict__,
        },
        "score": score(defect, control),
        "defect_run": defect,
        "control_run": control,
    }
    text = json.dumps(result, indent=2)
    print(json.dumps(result["score"], indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

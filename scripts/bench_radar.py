"""Radar hot-path cost per canonical event (Kafka I/O excluded).

    uv run python scripts/bench_radar.py --output evidence/benchmarks/m7-radar-hot-path.json

Generates one simulated hour of canonical events for 3,000 vehicles (with the
firmware_defect scenario), then times the radar's per-message path: byte check,
and JSON parse + window update only for events that carry a DTC. Best of 3.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path
from typing import Any

import orjson

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_stream.processor import CANONICAL, Normalizer
from prognos_stream.radar import Radar
from prognos_stream.radar_main import EMPTY_DTCS, cohorts_from_roster, event_ts
from prognos_stream.registry import VehicleRegistry

T0 = 1_790_000_000.0


class Capture:
    def __init__(self, normalizer: Normalizer) -> None:
        self.normalizer = normalizer
        self.now = T0
        self.events: list[bytes] = []

    def publish(self, topic: str, key: bytes, value: bytes, headers: list[Any]) -> None:
        if topic != "telemetry.raw":
            return
        hdrs = {k: v for k, v in headers if isinstance(v, bytes)}
        outcome = self.normalizer.process(value, hdrs, 0, self.now)
        if outcome.kind == CANONICAL:
            self.events.append(outcome.value)

    def poll(self) -> None:
        return None

    def flush(self, timeout: float = 30.0) -> int:
        return 0

    def stats(self) -> dict[str, int]:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vehicles", type=int, default=3000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    roster = generate_roster(args.vehicles, 20, 42)
    capture = Capture(Normalizer(VehicleRegistry.from_roster(roster)))
    cfg = SimConfig(
        vehicle_count=args.vehicles, events_per_second=args.vehicles / 10, tenant_count=20,
        mode=Mode.FAST, tick_hz=1, publisher=PublisherKind.NULL, metrics_port=0,
        burst_multiplier=1.0, scenario="firmware_defect",
    )  # fmt: skip
    sim = ShardSimulator(cfg, 0, roster.vehicles, capture, T0)
    for tick in range(3600):
        capture.now = T0 + tick
        sim.tick(capture.now, 1.0)
    events = capture.events

    best = float("inf")
    for _ in range(3):
        radar = Radar(cohorts_from_roster(roster))
        t0 = time.perf_counter()
        for value in events:
            if EMPTY_DTCS in value:
                continue
            e = orjson.loads(value)
            radar.add(e["vehicle_id"], event_ts(e["event_ts"]), e["dtc_codes"])
        best = min(best, time.perf_counter() - t0)
    result = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "what": "radar hot path per canonical event (byte check; parse + add only when a "
        "DTC is present), excluding Kafka I/O",
        "hardware": f"{platform.machine()} {platform.system()}, {os.cpu_count()} vCPU "
        "(not the target Mac)",
        "events": len(events),
        "events_with_dtc": sum(1 for v in events if EMPTY_DTCS not in v),
        "best_of_3_seconds": round(best, 3),
        "events_per_second": round(len(events) / best),
        "ns_per_event": round(best / len(events) * 1e9),
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

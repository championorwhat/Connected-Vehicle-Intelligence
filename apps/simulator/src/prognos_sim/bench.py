"""Standalone simulator benchmark (no Kafka): how fast can we generate events?

    uv run python -m prognos_sim.bench --vehicles 100000 --seconds 20 --workers 1,2,4

For each worker count, runs FAST mode with the null publisher (events are fully
built and serialised to JSON, then discarded) and records throughput and memory.
Results go to evidence/benchmarks/m3-simulator-benchmark.json.

What this measures: the simulator's own ceiling (physics + encoding +
anomaly injection + JSON serialisation). It does not measure Kafka; M4 does.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path

from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.main import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vehicles", type=int, default=100_000)
    parser.add_argument("--seconds", type=float, default=20.0, help="simulated seconds per run")
    parser.add_argument("--workers", default="1,2,4")
    parser.add_argument(
        "--output", type=Path, default=Path("evidence/benchmarks/m3-simulator-benchmark.json")
    )
    args = parser.parse_args(argv)

    runs = []
    for workers in (int(w) for w in args.workers.split(",")):
        cfg = SimConfig(
            vehicle_count=args.vehicles,
            events_per_second=float(args.vehicles),  # 1 event / vehicle / simulated second
            sim_workers=workers,
            mode=Mode.FAST,
            publisher=PublisherKind.NULL,
            duration_seconds=args.seconds,
            start_epoch=1_790_000_000.0,
            metrics_port=0,
            stats_interval_seconds=3600,
        )
        started = time.perf_counter()
        summary = run(cfg)
        wall = time.perf_counter() - started
        runs.append(
            {
                "workers": workers,
                "vehicles": args.vehicles,
                "simulated_seconds": args.seconds,
                "events_generated": summary["events_generated"],
                "wall_seconds_including_startup": round(wall, 2),
                "events_per_second": summary["events_per_second"],
                "events_per_second_per_worker": round(summary["events_per_second"] / workers, 1),
                "real_time_factor": round(summary["events_per_second"] / args.vehicles, 2),
                "avg_message_bytes": summary["avg_message_bytes"],
                "max_worker_rss_mb": summary["max_worker_rss_mb"],
                "anomalies": {
                    k: summary["totals"].get(k, 0)
                    for k in ("duplicates", "out_of_order", "malformed", "missing_field")
                },
            }
        )
        print(json.dumps(runs[-1]))

    result = {
        "benchmark": "simulator standalone (null publisher, JSON serialisation included)",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "machine": platform.machine(),
            "system": platform.system(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "note": "events_per_second is measured over worker wall time, which includes roster "
        "generation and fleet initialisation (~2 s per worker)",
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

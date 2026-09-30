"""Normalizer entry point.

    prognos-normalizer                       # config from environment
    EXIT_WHEN_IDLE_SECONDS=10 prognos-normalizer --summary out.json   # benchmarks

Registry source: PostgreSQL (default, system of record) or the deterministic
roster (REGISTRY_SOURCE=roster, for benchmarks without a database).
Scale out by running more processes/containers in the same consumer group, up to
the partition count of telemetry.raw.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
from pathlib import Path

from prometheus_client import start_http_server

from prognos_stream.processor import Normalizer
from prognos_stream.registry import VehicleRegistry
from prognos_stream.service import NormalizerService, ServiceConfig

log = logging.getLogger("prognos_stream")


def build_registry() -> VehicleRegistry:
    env = os.environ
    if env.get("REGISTRY_SOURCE", "postgres") == "roster":
        from prognos_common.roster import generate_roster

        roster = generate_roster(
            int(env.get("VEHICLE_COUNT", "10000")),
            int(env.get("TENANT_COUNT", "20")),
            int(env.get("SIM_SEED", env.get("SEED", "42"))),
        )
        return VehicleRegistry.from_roster(roster)
    dsn = (
        f"host={env.get('POSTGRES_HOST', 'localhost')} port={env.get('POSTGRES_PORT', '5432')} "
        f"dbname={env.get('POSTGRES_DB', 'prognos')} user={env.get('POSTGRES_USER', 'prognos')} "
        f"password={env['POSTGRES_PASSWORD']}"
    )
    return VehicleRegistry.from_postgres(dsn)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Prognos telemetry normalizer")
    parser.add_argument("--summary", type=Path, help="write a run summary JSON here on exit")
    args = parser.parse_args(argv)

    cfg = ServiceConfig.from_env()
    registry = build_registry()
    if not len(registry):
        log.warning("vehicle registry is empty: every event will go to the DLQ (seed the DB)")
    port = int(os.getenv("METRICS_PORT", "9102"))
    if port:
        start_http_server(port)
    service = NormalizerService(cfg, Normalizer(registry))
    service.install_signal_handlers()
    stats = service.run()

    counts = dict(service.normalizer.counts)
    busy = stats["busy_seconds"] or 1e-9
    summary = {
        "environment": {"machine": platform.machine(), "cpu_count": os.cpu_count()},
        "batch_size": cfg.batch_size,
        "consumed": int(stats["consumed"]),
        "wall_seconds": round(stats["wall_seconds"], 2),
        "busy_seconds": round(busy, 2),
        "messages_per_busy_second": round(stats["consumed"] / busy, 1),
        "outcomes": counts,
    }
    log.info(json.dumps({"event": "normalizer_summary", **summary}))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

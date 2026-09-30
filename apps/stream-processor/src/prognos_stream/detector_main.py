"""Detector entry point.

    prognos-detector                                  # config from environment
    EXIT_WHEN_IDLE_SECONDS=10 prognos-detector --summary out.json

Scale out by adding processes to the `detector` consumer group (up to the
partition count of telemetry.canonical).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from pathlib import Path

from prometheus_client import start_http_server

from prognos_stream.detector import Detector
from prognos_stream.detector_service import DetectorConfig, DetectorService

log = logging.getLogger("prognos_stream")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Prognos real-time detector")
    parser.add_argument("--summary", type=Path, help="write a run summary JSON here on exit")
    args = parser.parse_args(argv)

    cfg = DetectorConfig.from_env()
    port = int(os.getenv("METRICS_PORT", "9103"))
    if port:
        start_http_server(port)
    detector = Detector(state_interval_s=cfg.state_interval_s)
    latencies: list[float] = []
    service = DetectorService(cfg, detector)
    original_after_batch = service.after_batch

    def _capture(consumed: int, elapsed: float) -> None:
        latencies.extend(detector.alert_latencies)
        original_after_batch(consumed, elapsed)

    service.after_batch = _capture  # type: ignore[method-assign]
    service.install_signal_handlers()
    stats = service.run()

    busy = stats["busy_seconds"] or 1e-9
    latencies.sort()

    def pct(p: float) -> float | None:
        return round(latencies[int(p * (len(latencies) - 1))], 3) if latencies else None

    summary = {
        "consumed": int(stats["consumed"]),
        "wall_seconds": round(stats["wall_seconds"], 2),
        "messages_per_busy_second": round(stats["consumed"] / busy, 1),
        "vehicles_tracked": detector.vehicles_tracked(),
        "counts": detector.counts,
        "alert_latency_seconds": {
            "n": len(latencies),
            "p50": pct(0.5),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
        },
    }
    log.info(json.dumps({"event": "detector_summary", **summary}))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

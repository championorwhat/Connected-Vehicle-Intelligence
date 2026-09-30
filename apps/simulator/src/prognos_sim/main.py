"""Simulator entry point: shard the fleet across worker processes and run them.

    prognos-sim                                   # all settings from environment
    prognos-sim --mode fast --publisher null --duration 30 --summary out.json

Each worker process owns vehicles[worker_id::SIM_WORKERS] and 1/SIM_WORKERS
of the event rate, so throughput scales with processes, not threads (no GIL
contention). Workers report counters to the parent over a queue; the parent
aggregates them, exposes Prometheus metrics and writes a run summary.
SIGINT/SIGTERM trigger a graceful stop: workers release held-back events and
flush the producer before exiting.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import platform
import queue
import resource
import signal
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.publishers import FilePublisher, KafkaPublisher, NullPublisher, Publisher

log = logging.getLogger("prognos_sim")


def make_publisher(cfg: SimConfig, worker_id: int) -> Publisher:
    if cfg.publisher is PublisherKind.KAFKA:
        return KafkaPublisher(cfg.kafka_bootstrap_servers, f"prognos-sim-{worker_id}")
    if cfg.publisher is PublisherKind.FILE:
        return FilePublisher(cfg.output_dir, worker_id)
    return NullPublisher()


def run_worker(
    cfg: SimConfig,
    worker_id: int,
    start_ts: float,
    stop: Any,
    reports: Any,
) -> None:
    """Worker process body (also callable in-process for tests)."""
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # parent coordinates shutdown
    roster = generate_roster(cfg.vehicle_count, cfg.tenant_count, cfg.seed)
    vehicles = roster.vehicles[worker_id :: cfg.sim_workers]
    publisher = make_publisher(cfg, worker_id)
    sim = ShardSimulator(cfg, worker_id, vehicles, publisher, start_ts)

    dt = 1.0 / cfg.tick_hz
    wall_start = time.monotonic()
    last_report = wall_start
    tick_no = 0
    lag = 0.0
    while not stop.is_set():
        elapsed = tick_no * dt
        if cfg.duration_seconds and elapsed >= cfg.duration_seconds:
            break
        sim.tick(start_ts + elapsed, dt, cfg.rate_multiplier(elapsed))
        tick_no += 1
        now = time.monotonic()
        if cfg.mode is Mode.LIVE:
            ahead = (wall_start + tick_no * dt) - now
            if ahead > 0:
                time.sleep(ahead)
            lag = max(0.0, -ahead)
        if now - last_report >= cfg.stats_interval_seconds:
            reports.put((worker_id, {**sim.snapshot(), "sim_seconds": elapsed}, lag, False))
            last_report = now
    sim.finish()
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = usage if sys.platform == "darwin" else usage * 1024
    final = {
        **sim.snapshot(),
        "sim_seconds": tick_no * dt,
        "max_rss_bytes": rss_bytes,
        "wall_seconds": time.monotonic() - wall_start,
        "demo_vins": sim.demo_vins,
    }
    reports.put((worker_id, final, lag, True))


class _SimCollector(Collector):
    """Exposes aggregated worker counters as Prometheus metrics."""

    def __init__(self, state: dict[int, dict[str, Any]], lags: dict[int, float], cfg: SimConfig):
        self.state, self.lags, self.cfg = state, lags, cfg

    def collect(self) -> Iterator[Any]:
        totals = aggregate(self.state)
        for name, key, doc in (
            ("prognos_sim_events_generated", "events_generated", "Unique device events generated"),
            ("prognos_sim_messages_published", "published", "Messages handed to the publisher"),
            ("prognos_sim_messages_delivered", "delivered", "Messages acknowledged by the broker"),
            ("prognos_sim_delivery_failed", "delivery_failed", "Messages the broker rejected"),
            ("prognos_sim_duplicates", "duplicates", "Duplicate messages injected"),
            ("prognos_sim_out_of_order", "out_of_order", "Events held back and sent late"),
            ("prognos_sim_missing_field", "missing_field", "Events with a field removed"),
            ("prognos_sim_backpressure_waits", "backpressure_waits", "Producer queue-full waits"),
        ):
            yield CounterMetricFamily(name, doc, value=totals.get(key, 0))
        malformed = CounterMetricFamily(
            "prognos_sim_malformed", "Malformed payloads injected", labels=["kind"]
        )
        for key, value in totals.items():
            if key.startswith("malformed_"):
                malformed.add_metric([key.removeprefix("malformed_")], value)
        yield malformed
        truth = CounterMetricFamily(
            "prognos_sim_ground_truth", "Ground-truth records emitted", labels=["type"]
        )
        for kind in ("fault_onset", "failure"):
            truth.add_metric([kind], totals.get(f"ground_truth_{kind}", 0))
        yield truth
        yield GaugeMetricFamily(
            "prognos_sim_lag_seconds",
            "Max worker lag behind wall clock",
            value=max(self.lags.values(), default=0.0),
        )
        yield GaugeMetricFamily(
            "prognos_sim_target_events_per_second",
            "Configured base rate",
            value=self.cfg.events_per_second,
        )


def aggregate(state: dict[int, dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for snapshot in state.values():
        for key, value in snapshot.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value  # type: ignore[assignment]
    return totals


def run(cfg: SimConfig) -> dict[str, Any]:
    """Run all workers to completion (or until SIGINT/SIGTERM); return a summary."""
    start_ts = cfg.start_epoch or time.time()
    ctx = mp.get_context("spawn")
    stop = ctx.Event()
    reports: Any = ctx.Queue()
    state: dict[int, dict[str, Any]] = {}
    lags: dict[int, float] = {}
    if cfg.metrics_port:
        REGISTRY.register(_SimCollector(state, lags, cfg))
        start_http_server(cfg.metrics_port)

    def _stop(signum: int, _frame: object) -> None:
        log.info("signal %d received: stopping workers gracefully", signum)
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info(
        json.dumps({"event": "simulator_start", **{k: str(v) for k, v in asdict(cfg).items()}})
    )
    wall_start = time.monotonic()
    workers = [
        ctx.Process(target=run_worker, args=(cfg, w, start_ts, stop, reports), name=f"sim-{w}")
        for w in range(cfg.sim_workers)
    ]
    for proc in workers:
        proc.start()

    finished: set[int] = set()
    previous, previous_at = 0, wall_start
    while len(finished) < len(workers):
        try:
            worker_id, snapshot, lag, done = reports.get(timeout=1.0)
        except queue.Empty:
            if not any(p.is_alive() for p in workers):
                break
            continue
        state[worker_id], lags[worker_id] = snapshot, lag
        if done:
            finished.add(worker_id)
        now = time.monotonic()
        totals = aggregate(state)
        if now - previous_at >= cfg.stats_interval_seconds:
            rate = (totals.get("events_generated", 0) - previous) / (now - previous_at)
            log.info(
                json.dumps(
                    {
                        "event": "progress",
                        "events_per_second": round(rate),
                        "lag_s": round(max(lags.values()), 2),
                        **totals,
                    }
                )
            )
            previous, previous_at = totals.get("events_generated", 0), now
    for proc in workers:
        proc.join(timeout=30)

    wall = time.monotonic() - wall_start
    totals = aggregate(state)
    summary = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "machine": platform.machine(),
            "system": platform.system(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "config": {k: str(v) for k, v in asdict(cfg).items()},
        "wall_seconds": round(wall, 3),
        "events_generated": totals.get("events_generated", 0),
        "events_per_second": round(totals.get("events_generated", 0) / wall, 1) if wall else 0,
        "messages_published": totals.get("published", 0),
        "bytes_published": totals.get("bytes", 0),
        "avg_message_bytes": round(totals.get("bytes", 0) / max(totals.get("published", 1), 1), 1),
        "max_worker_rss_mb": round(
            max((s.get("max_rss_bytes", 0) for s in state.values()), default=0) / 2**20, 1
        ),
        "max_lag_seconds": round(max(lags.values(), default=0.0), 3),
        "demo_vins": [vin for s in state.values() for vin in s.get("demo_vins", [])],
        "totals": totals,
        "workers_completed": len(finished),
    }
    log.info(json.dumps({"event": "simulator_summary", **summary}))
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
    parser = argparse.ArgumentParser(description="Prognos vehicle telemetry simulator")
    parser.add_argument("--mode", choices=[m.value for m in Mode])
    parser.add_argument("--publisher", choices=[p.value for p in PublisherKind])
    parser.add_argument("--vehicles", type=int)
    parser.add_argument("--rate", type=float, help="events per second (base, before bursts)")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--duration", type=float, help="seconds of simulated time; 0 = forever")
    parser.add_argument("--summary", type=Path, help="write the run summary JSON here")
    args = parser.parse_args(argv)

    cfg = SimConfig.from_env()
    overrides = {
        "mode": Mode(args.mode) if args.mode else None,
        "publisher": PublisherKind(args.publisher) if args.publisher else None,
        "vehicle_count": args.vehicles,
        "events_per_second": args.rate,
        "sim_workers": args.workers,
        "duration_seconds": args.duration,
    }
    cfg = replace(cfg, **{k: v for k, v in overrides.items() if v is not None})
    summary = run(cfg)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if summary["workers_completed"] == cfg.sim_workers else 1


if __name__ == "__main__":
    sys.exit(main())

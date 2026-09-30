"""Sink service: alerts -> PostgreSQL (+ Redis pub/sub); vehicle.state -> Redis.

    prognos-sink            # config from environment (POSTGRES_*, REDIS_*, KAFKA_*)

Buffers one Kafka batch, writes it to the stores in `before_commit`, then commits
offsets. Stores are idempotent, so redelivery after a crash is harmless.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson
import psycopg
import redis
from prometheus_client import Counter, Histogram, start_http_server

from prognos_common.logs import configure
from prognos_stream.kafka_loop import BatchService, LoopConfig, Record
from prognos_stream.sinks import AlertStore, LiveStateStore

log = logging.getLogger("prognos_stream")

WRITES = Counter("prognos_sink_writes", "Records written", ["store"])
REJECTS = Counter("prognos_sink_rejected", "Records rejected by a store")
WRITE_SECONDS = Histogram(
    "prognos_sink_write_seconds", "Time to write one batch to the stores",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)  # fmt: skip


@dataclass
class SinkConfig(LoopConfig):
    group_id: str = "sink"
    input_topic: str = "alerts,vehicle.state"
    alerts_topic: str = "alerts"
    state_topic: str = "vehicle.state"
    retry_budget_s: float = 60.0

    @classmethod
    def from_env(cls) -> SinkConfig:
        env = os.environ
        return cls(
            bootstrap_servers=env.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            group_id=env.get("SINK_GROUP_ID", cls.group_id),
            batch_size=int(env.get("SINK_BATCH_SIZE", cls.batch_size)),
            exit_when_idle_s=float(env.get("EXIT_WHEN_IDLE_SECONDS", cls.exit_when_idle_s)),
            retry_budget_s=float(env.get("SINK_RETRY_BUDGET_SECONDS", cls.retry_budget_s)),
        )


class SinkService(BatchService):
    service_name = "sink"

    def __init__(self, cfg: SinkConfig, alerts: AlertStore, live: LiveStateStore) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.alert_store = alerts
        self.live_store = live
        self.pending_alerts: list[dict[str, Any]] = []
        self.pending_states: dict[str, dict[str, Any]] = {}  # latest per vehicle in the batch

    def handle(self, msg: Any, now: float) -> Iterable[Record]:
        try:
            body = orjson.loads(msg.value())
        except orjson.JSONDecodeError:
            REJECTS.inc()
            return ()
        if msg.topic() == self.cfg.alerts_topic:
            self.pending_alerts.append(body)
        else:
            self.pending_states[body["vehicle_id"]] = body
        return ()

    def before_commit(self) -> None:
        with WRITE_SECONDS.time():
            rejected_before = self.alert_store.stats["rejected"]
            self.alert_store.write(self.pending_alerts)
            self.live_store.write(list(self.pending_states.values()), self.pending_alerts)
        WRITES.labels("postgres").inc(len(self.pending_alerts))
        WRITES.labels("redis").inc(len(self.pending_states))
        REJECTS.inc(self.alert_store.stats["rejected"] - rejected_before)
        self.pending_alerts = []
        self.pending_states = {}


def _dsn() -> str:
    env = os.environ
    return (
        f"host={env.get('POSTGRES_HOST', 'localhost')} port={env.get('POSTGRES_PORT', '5432')} "
        f"dbname={env.get('POSTGRES_DB', 'prognos')} user={env.get('POSTGRES_USER', 'prognos')} "
        f"password={env['POSTGRES_PASSWORD']}"
    )


def main(argv: list[str] | None = None) -> int:
    configure("sink")
    parser = argparse.ArgumentParser(description="Prognos sink (PostgreSQL + Redis)")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    cfg = SinkConfig.from_env()
    port = int(os.getenv("METRICS_PORT", "9104"))
    if port:
        start_http_server(port)
    client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")),
        socket_timeout=5, socket_connect_timeout=5,
    )  # fmt: skip
    alerts = AlertStore(_dsn(), retry_budget_s=cfg.retry_budget_s)
    live = LiveStateStore(client, retry_budget_s=cfg.retry_budget_s)
    service = SinkService(cfg, alerts, live)
    service.install_signal_handlers()
    try:
        stats = service.run()
    except (psycopg.OperationalError, redis.ConnectionError, redis.TimeoutError) as exc:
        # Retry budget spent: exit without committing, so the batch is re-read after the
        # restart (docker restart policy / Kubernetes). Nothing is lost; M13 chaos drill.
        log.error("store unavailable for %.0fs, exiting for a restart (offsets not committed):"
                  " %s", cfg.retry_budget_s, exc)  # fmt: skip
        return 1
    finally:
        alerts.close()
    summary = {"consumed": int(stats["consumed"]), "postgres": alerts.stats, "redis": live.stats}
    log.info(json.dumps({"event": "sink_summary", **summary}))
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

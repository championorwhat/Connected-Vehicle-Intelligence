"""Normalizer service: telemetry.raw -> telemetry.canonical | telemetry.dlq.

The consume/produce/commit mechanics (at-least-once, back-pressure, graceful
shutdown, lag metrics) live in `kafka_loop.BatchService`; this module only maps
one raw message to its output record.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from prometheus_client import Counter, Gauge

from prognos_stream.kafka_loop import BatchService, DeliveryError, LoopConfig, Record
from prognos_stream.processor import CANONICAL, DLQ, Normalizer

__all__ = ["DeliveryError", "NormalizerService", "ServiceConfig"]

OUTCOMES = Counter("prognos_normalizer_outcomes", "Normalisation outcomes", ["kind"])
DLQ_REASONS = Counter("prognos_normalizer_dlq", "Messages sent to the DLQ", ["reason"])
LATE = Counter("prognos_normalizer_late_events", "Out-of-order events forwarded")
REGISTRY_SIZE = Gauge("prognos_normalizer_registry_vehicles", "Vehicles in the VIN registry")


@dataclass
class ServiceConfig(LoopConfig):
    group_id: str = "normalizer"
    input_topic: str = "telemetry.raw"
    canonical_topic: str = "telemetry.canonical"
    dlq_topic: str = "telemetry.dlq"
    registry_refresh_s: float = 300.0

    @property
    def raw_topic(self) -> str:
        return self.input_topic

    @classmethod
    def from_env(cls) -> ServiceConfig:
        env = os.environ
        return cls(
            bootstrap_servers=env.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            group_id=env.get("NORMALIZER_GROUP_ID", cls.group_id),
            batch_size=int(env.get("NORMALIZER_BATCH_SIZE", cls.batch_size)),
            poll_timeout_s=float(env.get("NORMALIZER_POLL_TIMEOUT_SECONDS", cls.poll_timeout_s)),
            registry_refresh_s=float(env.get("REGISTRY_REFRESH_SECONDS", cls.registry_refresh_s)),
            exit_when_idle_s=float(env.get("EXIT_WHEN_IDLE_SECONDS", cls.exit_when_idle_s)),
        )


class NormalizerService(BatchService):
    service_name = "normalizer"

    def __init__(self, cfg: ServiceConfig, normalizer: Normalizer) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.normalizer = normalizer
        self._before: dict[str, int] = {}
        REGISTRY_SIZE.set(len(normalizer.registry))

    def on_partitions_revoked(self, partitions: list[int]) -> None:
        for partition in partitions:
            self.normalizer.forget_partition(partition)

    def on_idle(self) -> None:
        self.normalizer.registry.refresh_if_older_than(self.cfg.registry_refresh_s)
        REGISTRY_SIZE.set(len(self.normalizer.registry))

    def handle(self, msg: Any, now: float) -> Iterable[Record]:
        raw_headers = msg.headers() or []
        headers = {k: v for k, v in raw_headers if isinstance(v, bytes)}
        outcome = self.normalizer.process(msg.value(), headers, msg.partition(), now)
        if outcome.kind == CANONICAL:
            return ((self.cfg.canonical_topic, outcome.key, outcome.value, None),)
        if outcome.kind == DLQ:
            dlq_headers = [
                *raw_headers,
                ("dlq.reason", outcome.reason.encode()),
                ("dlq.detail", outcome.detail[:200].encode()),
                ("dlq.source", f"{msg.topic()}/{msg.partition()}/{msg.offset()}".encode()),
                ("dlq.at", str(int(now * 1000)).encode()),
            ]
            return ((self.cfg.dlq_topic, msg.key(), msg.value() or b"", dlq_headers),)
        return ()

    def after_batch(self, consumed: int, elapsed: float) -> None:
        counts = self.normalizer.counts
        for key, value in counts.items():
            delta = value - self._before.get(key, 0)
            if not delta:
                continue
            if key.startswith("dlq_"):
                DLQ_REASONS.labels(key.removeprefix("dlq_")).inc(delta)
            elif key == "late":
                LATE.inc(delta)
            else:
                OUTCOMES.labels(key).inc(delta)
        self._before = dict(counts)

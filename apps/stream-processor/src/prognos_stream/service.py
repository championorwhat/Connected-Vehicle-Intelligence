"""Kafka loop around the pure Normalizer.

Delivery semantics: at-least-once. Per batch:
  consume up to BATCH_SIZE -> process -> produce canonical / DLQ records
  -> producer.flush() -> all delivered? -> commit consumed offsets (synchronous)
If any produce fails, the process exits WITHOUT committing, so the batch is
re-consumed after restart. Duplicates this may create are absorbed downstream
by the deterministic event_id. We do not claim exactly-once.

Back-pressure: the loop is synchronous, so a slow broker slows consumption
(BufferError -> poll and retry) instead of growing memory without bound.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from prometheus_client import Counter, Gauge, Histogram

from prognos_stream.processor import CANONICAL, DLQ, Normalizer

log = logging.getLogger(__name__)

CONSUMED = Counter("prognos_normalizer_consumed", "Raw messages consumed")
OUTCOMES = Counter("prognos_normalizer_outcomes", "Normalisation outcomes", ["kind"])
DLQ_REASONS = Counter("prognos_normalizer_dlq", "Messages sent to the DLQ", ["reason"])
LATE = Counter("prognos_normalizer_late_events", "Out-of-order events forwarded")
BATCH_SECONDS = Histogram(
    "prognos_normalizer_batch_seconds",
    "Wall time per batch (process + produce + flush + commit)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
LAG = Gauge("prognos_normalizer_consumer_lag", "Messages behind the log end", ["partition"])
REGISTRY_SIZE = Gauge("prognos_normalizer_registry_vehicles", "Vehicles in the VIN registry")


@dataclass
class ServiceConfig:
    bootstrap_servers: str = "localhost:9092"
    group_id: str = "normalizer"
    raw_topic: str = "telemetry.raw"
    canonical_topic: str = "telemetry.canonical"
    dlq_topic: str = "telemetry.dlq"
    batch_size: int = 2_000
    poll_timeout_s: float = 0.1  # caps batching delay at low rates; batches fill fast at high rates
    registry_refresh_s: float = 300.0
    exit_when_idle_s: float = 0.0  # >0: stop after this long with nothing to consume
    lag_interval_s: float = 10.0
    client_id: str = field(default_factory=lambda: f"normalizer-{os.getpid()}")

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


class DeliveryError(RuntimeError):
    pass


class NormalizerService:
    def __init__(self, cfg: ServiceConfig, normalizer: Normalizer) -> None:
        self.cfg = cfg
        self.normalizer = normalizer
        self.stopping = False
        self.delivery_errors = 0
        self.assigned_at: float | None = None  # idle-exit counts only while partitions are held
        self.consumer = Consumer(
            {
                "bootstrap.servers": cfg.bootstrap_servers,
                "group.id": cfg.group_id,
                "client.id": cfg.client_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
                "partition.assignment.strategy": "cooperative-sticky",
                "max.poll.interval.ms": 300_000,
                "fetch.min.bytes": 64 * 1024,
                "fetch.wait.max.ms": 100,
            }
        )
        self.producer = Producer(
            {
                "bootstrap.servers": cfg.bootstrap_servers,
                "client.id": cfg.client_id,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "lz4",
                "linger.ms": 10,
                "batch.size": 512 * 1024,
            }
        )
        self.stats: dict[str, float] = {"consumed": 0, "batches": 0, "busy_seconds": 0.0}

    # ------------------------------------------------------------------ lifecycle
    def install_signal_handlers(self) -> None:
        def _stop(signum: int, _frame: object) -> None:
            log.info("signal %d: finishing current batch, then stopping", signum)
            self.stopping = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    def _on_revoke(self, _consumer: Any, partitions: list[TopicPartition]) -> None:
        for tp in partitions:
            self.normalizer.forget_partition(tp.partition)
            with contextlib.suppress(KeyError):  # lag not reported yet for this partition
                LAG.remove(str(tp.partition))
        log.info("partitions revoked: %s", [tp.partition for tp in partitions])

    def _on_assign(self, _consumer: Any, partitions: list[TopicPartition]) -> None:
        if partitions and self.assigned_at is None:
            self.assigned_at = time.monotonic()
        log.info("partitions assigned: %s", [tp.partition for tp in partitions])

    def _on_delivery(self, err: Any, _msg: Any) -> None:
        if err is not None:
            self.delivery_errors += 1
            log.error("produce failed: %s", err)

    def run(self) -> dict[str, float]:
        self.consumer.subscribe(
            [self.cfg.raw_topic], on_assign=self._on_assign, on_revoke=self._on_revoke
        )
        REGISTRY_SIZE.set(len(self.normalizer.registry))
        started = time.monotonic()
        last_message = last_lag = started
        try:
            while not self.stopping:
                messages = self.consumer.consume(self.cfg.batch_size, self.cfg.poll_timeout_s)
                now_mono = time.monotonic()
                if now_mono - last_lag >= self.cfg.lag_interval_s:
                    self._report_lag()
                    last_lag = now_mono
                if not messages:
                    idle = self.cfg.exit_when_idle_s
                    idle_since = max(last_message, self.assigned_at or now_mono)
                    if idle and self.assigned_at is not None and now_mono - idle_since >= idle:
                        log.info("idle for %.0fs: exiting", idle)
                        break
                    self.normalizer.registry.refresh_if_older_than(self.cfg.registry_refresh_s)
                    REGISTRY_SIZE.set(len(self.normalizer.registry))
                    continue
                last_message = now_mono
                self._process_batch(messages)
        finally:
            self.producer.flush(30)
            self.consumer.close()
        self.stats["wall_seconds"] = time.monotonic() - started
        return self.stats

    # ------------------------------------------------------------------ batch
    def _produce(self, topic: str, key: bytes | None, value: bytes, headers: Any = None) -> None:
        while True:
            try:
                self.producer.produce(
                    topic, value=value, key=key, headers=headers, on_delivery=self._on_delivery
                )
                return
            except BufferError:
                self.producer.poll(0.05)

    def _process_batch(self, messages: list[Any]) -> None:
        t0 = time.monotonic()
        now = time.time()
        before = dict(self.normalizer.counts)
        consumed = 0
        for msg in messages:
            error = msg.error()
            if error is not None:
                if error.code() == KafkaError._PARTITION_EOF:
                    continue
                if error.fatal():
                    raise RuntimeError(f"fatal consumer error: {error}")
                log.warning("consumer error: %s", error)
                continue
            consumed += 1
            raw_headers = msg.headers() or []
            headers = {k: v for k, v in raw_headers if isinstance(v, bytes)}
            outcome = self.normalizer.process(msg.value(), headers, msg.partition(), now)
            if outcome.kind == CANONICAL:
                self._produce(self.cfg.canonical_topic, outcome.key, outcome.value)
            elif outcome.kind == DLQ:
                dlq_headers = [
                    *raw_headers,
                    ("dlq.reason", outcome.reason.encode()),
                    ("dlq.detail", outcome.detail[:200].encode()),
                    ("dlq.source", f"{msg.topic()}/{msg.partition()}/{msg.offset()}".encode()),
                    ("dlq.at", str(int(now * 1000)).encode()),
                ]
                self._produce(self.cfg.dlq_topic, msg.key(), msg.value() or b"", dlq_headers)
        remaining = self.producer.flush(30)
        if remaining or self.delivery_errors:
            raise DeliveryError(
                f"{remaining} undelivered, {self.delivery_errors} failed: not committing batch"
            )
        self.consumer.commit(asynchronous=False)

        elapsed = time.monotonic() - t0
        BATCH_SECONDS.observe(elapsed)
        CONSUMED.inc(consumed)
        after = self.normalizer.counts
        for key, value in after.items():
            delta = value - before.get(key, 0)
            if not delta:
                continue
            if key.startswith("dlq_"):
                DLQ_REASONS.labels(key.removeprefix("dlq_")).inc(delta)
            elif key == "late":
                LATE.inc(delta)
            else:
                OUTCOMES.labels(key).inc(delta)
        self.stats["consumed"] += consumed
        self.stats["batches"] += 1
        self.stats["busy_seconds"] += elapsed

    def _report_lag(self) -> None:
        try:
            assignment = self.consumer.assignment()
            if not assignment:
                return
            positions = self.consumer.position(assignment)
            for tp in positions:
                _, high = self.consumer.get_watermark_offsets(tp, timeout=2, cached=False)
                position = tp.offset if tp.offset >= 0 else 0
                LAG.labels(str(tp.partition)).set(max(0, high - position))
        except Exception:  # lag reporting must never break processing
            log.debug("lag report failed", exc_info=True)

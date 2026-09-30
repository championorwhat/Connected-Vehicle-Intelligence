"""Reusable at-least-once Kafka batch loop shared by the normalizer and the detector.

Per batch:
  consume up to batch_size -> handle() each message -> produce its outputs
  -> producer.flush() -> all delivered? -> commit offsets (synchronous)
A failed produce raises DeliveryError before the commit, so the batch is
re-consumed after restart; outputs carry deterministic ids so sinks stay idempotent.

Subclasses implement `handle(msg, now)` (return records to produce) and may
override `on_partitions_revoked` (drop per-partition state) and `on_idle`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
from prometheus_client import Counter, Gauge, Histogram

log = logging.getLogger(__name__)

CONSUMED = Counter("prognos_stream_consumed", "Messages consumed", ["service"])
BATCH_SECONDS = Histogram(
    "prognos_stream_batch_seconds",
    "Wall time per batch (handle + produce + flush + commit)",
    ["service"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
LAG = Gauge("prognos_stream_consumer_lag", "Messages behind the log end", ["service", "partition"])

# (topic, key, value, headers)
Record = tuple[str, bytes | None, bytes, list[tuple[str, str | bytes | None]] | None]


@dataclass
class LoopConfig:
    bootstrap_servers: str = "localhost:9092"
    group_id: str = "service"
    input_topic: str = "telemetry.raw"  # comma-separated for several topics
    batch_size: int = 2_000
    poll_timeout_s: float = 0.1  # caps batching delay at low rates
    exit_when_idle_s: float = 0.0  # >0: stop after this long idle while holding partitions
    lag_interval_s: float = 10.0
    client_id: str = field(default_factory=lambda: f"prognos-{os.getpid()}")


class DeliveryError(RuntimeError):
    pass


class BatchService:
    service_name = "service"

    def __init__(self, loop_cfg: LoopConfig) -> None:
        self.loop_cfg = loop_cfg
        self.stopping = False
        self.delivery_errors = 0
        self.assigned_at: float | None = None
        self.consumer = Consumer(
            {
                "bootstrap.servers": loop_cfg.bootstrap_servers,
                "group.id": loop_cfg.group_id,
                "client.id": loop_cfg.client_id,
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
                "bootstrap.servers": loop_cfg.bootstrap_servers,
                "client.id": loop_cfg.client_id,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "lz4",
                "linger.ms": 5,
                "batch.size": 512 * 1024,
            }
        )
        self.stats: dict[str, float] = {"consumed": 0, "batches": 0, "busy_seconds": 0.0}

    # ------------------------------------------------------------------ hooks
    def handle(self, msg: Any, now: float) -> Iterable[Record]:
        raise NotImplementedError

    def on_partitions_revoked(self, partitions: list[int]) -> None:
        """Drop per-partition state; the new owner rebuilds it."""

    def on_idle(self) -> None:
        """Called when a poll returns nothing (housekeeping)."""

    def before_commit(self) -> None:
        """Called after outputs are flushed and before offsets are committed.

        Sinks write their buffered batch here; raising prevents the commit, so the
        batch is re-consumed (at-least-once).
        """

    def after_batch(self, consumed: int, elapsed: float) -> None:
        """Called after each committed batch (metrics)."""

    # ------------------------------------------------------------------ lifecycle
    def install_signal_handlers(self) -> None:
        def _stop(signum: int, _frame: object) -> None:
            log.info("signal %d: finishing current batch, then stopping", signum)
            self.stopping = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    def _on_revoke(self, _consumer: Any, partitions: list[TopicPartition]) -> None:
        self.on_partitions_revoked([tp.partition for tp in partitions])
        for tp in partitions:
            with contextlib.suppress(KeyError):  # lag not reported yet
                LAG.remove(self.service_name, str(tp.partition))
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
        cfg = self.loop_cfg
        topics = [t.strip() for t in cfg.input_topic.split(",") if t.strip()]
        self.consumer.subscribe(topics, on_assign=self._on_assign, on_revoke=self._on_revoke)
        started = time.monotonic()
        last_message = last_lag = started
        try:
            while not self.stopping:
                messages = self.consumer.consume(cfg.batch_size, cfg.poll_timeout_s)
                now_mono = time.monotonic()
                if now_mono - last_lag >= cfg.lag_interval_s:
                    self._report_lag()
                    last_lag = now_mono
                if not messages:
                    idle_since = max(last_message, self.assigned_at or now_mono)
                    if (
                        cfg.exit_when_idle_s
                        and self.assigned_at is not None
                        and now_mono - idle_since >= cfg.exit_when_idle_s
                    ):
                        log.info("idle for %.0fs: exiting", cfg.exit_when_idle_s)
                        break
                    self.on_idle()
                    continue
                last_message = now_mono
                self._process_batch(messages)
        finally:
            self.producer.flush(30)
            self.consumer.close()
        self.stats["wall_seconds"] = time.monotonic() - started
        return self.stats

    # ------------------------------------------------------------------ batch
    def produce(self, record: Record) -> None:
        topic, key, value, headers = record
        while True:
            try:
                self.producer.produce(
                    topic, value=value, key=key, headers=headers, on_delivery=self._on_delivery
                )
                return
            except BufferError:  # back-pressure: wait for the broker instead of buffering more
                self.producer.poll(0.05)

    def _process_batch(self, messages: list[Any]) -> None:
        t0 = time.monotonic()
        now = time.time()
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
            for record in self.handle(msg, now):
                self.produce(record)
        remaining = self.producer.flush(30)
        if remaining or self.delivery_errors:
            raise DeliveryError(
                f"{remaining} undelivered, {self.delivery_errors} failed: not committing batch"
            )
        self.before_commit()
        self.consumer.commit(asynchronous=False)
        elapsed = time.monotonic() - t0
        BATCH_SECONDS.labels(self.service_name).observe(elapsed)
        CONSUMED.labels(self.service_name).inc(consumed)
        self.stats["consumed"] += consumed
        self.stats["batches"] += 1
        self.stats["busy_seconds"] += elapsed
        self.after_batch(consumed, elapsed)

    def _report_lag(self) -> None:
        try:
            assignment = self.consumer.assignment()
            if not assignment:
                return
            for tp in self.consumer.position(assignment):
                _, high = self.consumer.get_watermark_offsets(tp, timeout=2, cached=False)
                position = tp.offset if tp.offset >= 0 else 0
                LAG.labels(self.service_name, str(tp.partition)).set(max(0, high - position))
        except Exception:  # lag reporting must never break processing
            log.debug("lag report failed", exc_info=True)

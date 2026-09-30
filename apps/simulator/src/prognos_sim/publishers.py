"""Where simulated events go: Kafka, a file, memory (tests) or nowhere (benchmarks)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

Headers = list[tuple[str, str | bytes | None]]
log = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish(self, topic: str, key: bytes, value: bytes, headers: Headers) -> None: ...
    def poll(self) -> None: ...
    def flush(self, timeout: float = 30.0) -> int: ...
    def stats(self) -> dict[str, int]: ...


class NullPublisher:
    """Discards events but counts them: isolates generation + serialisation cost."""

    def __init__(self) -> None:
        self.messages = 0
        self.bytes = 0

    def publish(self, topic: str, key: bytes, value: bytes, headers: Headers) -> None:
        self.messages += 1
        self.bytes += len(value)

    def poll(self) -> None:
        return None

    def flush(self, timeout: float = 30.0) -> int:
        return 0

    def stats(self) -> dict[str, int]:
        return {"published": self.messages, "bytes": self.bytes, "delivered": self.messages}


class MemoryPublisher(NullPublisher):
    """Keeps every message; for tests."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[tuple[str, bytes, bytes, Headers]] = []

    def publish(self, topic: str, key: bytes, value: bytes, headers: Headers) -> None:
        super().publish(topic, key, value, headers)
        self.records.append((topic, key, value, headers))


class FilePublisher(NullPublisher):
    """Writes one JSON value per line to <dir>/<topic>-w<worker>.jsonl."""

    def __init__(self, directory: str, worker_id: int) -> None:
        super().__init__()
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.worker_id = worker_id
        self.files: dict[str, object] = {}

    def publish(self, topic: str, key: bytes, value: bytes, headers: Headers) -> None:
        super().publish(topic, key, value, headers)
        handle = self.files.get(topic)
        if handle is None:
            handle = open(self.dir / f"{topic}-w{self.worker_id}.jsonl", "ab")  # noqa: SIM115
            self.files[topic] = handle
        handle.write(value.replace(b"\n", b" ") + b"\n")  # type: ignore[attr-defined]

    def flush(self, timeout: float = 30.0) -> int:
        for handle in self.files.values():
            handle.flush()  # type: ignore[attr-defined]
        return 0


class KafkaPublisher:
    """Idempotent, compressed, batched Kafka producer with back-pressure.

    acks=all + enable.idempotence: a broker retry cannot duplicate or reorder
    messages within a partition. When the local queue is full, produce() raises
    BufferError; we poll (serving delivery callbacks) and retry, which slows the
    simulator down to what Kafka accepts instead of dropping events.
    """

    def __init__(self, bootstrap_servers: str, client_id: str) -> None:
        from confluent_kafka import Producer

        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "client.id": client_id,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "lz4",
                "linger.ms": 25,
                "batch.size": 512 * 1024,
                "queue.buffering.max.kbytes": 64 * 1024,
                "queue.buffering.max.messages": 200_000,
            }
        )
        self.published = 0
        self.delivered = 0
        self.failed = 0
        self.bytes = 0
        self.backpressure_waits = 0

    def _on_delivery(self, err: object, _msg: object) -> None:
        if err is None:
            self.delivered += 1
        else:
            self.failed += 1
            log.error("delivery failed: %s", err)

    def publish(self, topic: str, key: bytes, value: bytes, headers: Headers) -> None:
        while True:
            try:
                self.producer.produce(
                    topic, value=value, key=key, headers=headers, on_delivery=self._on_delivery
                )
                break
            except BufferError:
                self.backpressure_waits += 1
                self.producer.poll(0.05)
        self.published += 1
        self.bytes += len(value)

    def poll(self) -> None:
        self.producer.poll(0)

    def flush(self, timeout: float = 30.0) -> int:
        deadline = time.monotonic() + timeout
        remaining = self.producer.flush(timeout)
        while remaining and time.monotonic() < deadline:
            remaining = self.producer.flush(1.0)
        return int(remaining)

    def stats(self) -> dict[str, int]:
        return {
            "published": self.published,
            "delivered": self.delivered,
            "delivery_failed": self.failed,
            "bytes": self.bytes,
            "backpressure_waits": self.backpressure_waits,
        }

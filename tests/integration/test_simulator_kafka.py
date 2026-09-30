"""The simulator's Kafka publisher against a real broker: delivery, keys, headers, back-pressure."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from typing import Any

import orjson
import pytest
from confluent_kafka import Consumer
from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]
from testcontainers.core.container import DockerContainer

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.publishers import KafkaPublisher


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def kafka_bootstrap() -> Iterator[str]:
    port = _free_port()
    container = (
        DockerContainer("apache/kafka:4.1.0")
        .with_bind_ports(9092, port)
        .with_env("KAFKA_NODE_ID", "1")
        .with_env("KAFKA_PROCESS_ROLES", "broker,controller")
        .with_env("KAFKA_CONTROLLER_QUORUM_VOTERS", "1@localhost:9093")
        .with_env("KAFKA_LISTENERS", "PLAINTEXT://:9092,CONTROLLER://:9093")
        .with_env("KAFKA_ADVERTISED_LISTENERS", f"PLAINTEXT://localhost:{port}")
        .with_env(
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT"
        )
        .with_env("KAFKA_CONTROLLER_LISTENER_NAMES", "CONTROLLER")
        .with_env("KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", "1")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1")
    )
    with container:
        bootstrap = f"localhost:{port}"
        admin = AdminClient({"bootstrap.servers": bootstrap})
        for _ in range(60):
            try:
                admin.list_topics(timeout=2)
                break
            except Exception:  # broker still starting
                time.sleep(1)
        futures = admin.create_topics(
            [NewTopic("telemetry.raw", 3, 1), NewTopic("sim.truth", 1, 1)]
        )
        for future in futures.values():
            future.result(timeout=30)
        yield bootstrap


def _consume_all(bootstrap: str, topic: str, expected: int) -> list[Any]:
    consumer = Consumer(
        {"bootstrap.servers": bootstrap, "group.id": f"test-{topic}-{time.time()}",
         "auto.offset.reset": "earliest", "enable.auto.commit": False}
    )  # fmt: skip
    consumer.subscribe([topic])
    messages: list[Any] = []
    deadline = time.monotonic() + 60
    while len(messages) < expected and time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is not None and msg.error() is None:
            messages.append(msg)
    consumer.close()
    return messages


def test_simulator_delivers_every_message_to_kafka(kafka_bootstrap: str) -> None:
    cfg = SimConfig(
        vehicle_count=500, events_per_second=500.0, tenant_count=5, mode=Mode.FAST,
        publisher=PublisherKind.KAFKA, kafka_bootstrap_servers=kafka_bootstrap,
        fault_rate=0.05, metrics_port=0,
    )  # fmt: skip
    roster = generate_roster(cfg.vehicle_count, cfg.tenant_count, cfg.seed)
    publisher = KafkaPublisher(kafka_bootstrap, "test-sim")
    sim = ShardSimulator(cfg, 0, roster.vehicles, publisher, 1_790_000_000.0)
    for tick in range(100):  # 10 simulated seconds
        sim.tick(1_790_000_000.0 + tick * 0.1, 0.1)
    sim.finish()

    stats = sim.snapshot()
    raw_expected = sim.counts["events_generated"] + sim.counts["duplicates"]
    assert stats["delivered"] == stats["published"]
    assert stats["delivery_failed"] == 0

    raw = _consume_all(kafka_bootstrap, "telemetry.raw", raw_expected)
    assert len(raw) == raw_expected
    # Keys are VINs, so each vehicle's events share one partition (per-vehicle ordering).
    partitions_per_key: dict[bytes, set[int]] = {}
    for msg in raw:
        partitions_per_key.setdefault(msg.key(), set()).add(msg.partition())
    assert all(len(p) == 1 for p in partitions_per_key.values())
    oems = {dict(msg.headers())["oem"] for msg in raw}
    assert {b"ORION", b"VEGA", b"LYRA"} <= oems

    truth = _consume_all(kafka_bootstrap, "sim.truth", sim.counts["ground_truth_fault_onset"])
    assert truth
    record = orjson.loads(truth[0].value())
    assert record["type"] == "FAULT_ONSET"
    assert record["failure_mode"]

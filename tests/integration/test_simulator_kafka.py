"""The simulator's Kafka publisher against a real broker: delivery, keys, headers, back-pressure."""

from __future__ import annotations

import time
from typing import Any

import orjson
from confluent_kafka import Consumer

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.publishers import KafkaPublisher


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


def test_simulator_delivers_every_message_to_kafka(kafka_bootstrap: str, fresh_topics) -> None:  # type: ignore[no-untyped-def]
    topics = fresh_topics("telemetry.raw", "sim.truth")
    cfg = SimConfig(
        vehicle_count=500, events_per_second=500.0, tenant_count=5, mode=Mode.FAST,
        publisher=PublisherKind.KAFKA, kafka_bootstrap_servers=kafka_bootstrap,
        fault_rate=0.05, metrics_port=0, raw_topic=topics["telemetry.raw"],
        ground_truth_topic=topics["sim.truth"],
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

    raw = _consume_all(kafka_bootstrap, topics["telemetry.raw"], raw_expected)
    assert len(raw) == raw_expected
    # Keys are VINs, so each vehicle's events share one partition (per-vehicle ordering).
    partitions_per_key: dict[bytes, set[int]] = {}
    for msg in raw:
        partitions_per_key.setdefault(msg.key(), set()).add(msg.partition())
    assert all(len(p) == 1 for p in partitions_per_key.values())
    oems = {dict(msg.headers())["oem"] for msg in raw}
    assert {b"ORION", b"VEGA", b"LYRA"} <= oems

    truth = _consume_all(
        kafka_bootstrap, topics["sim.truth"], sim.counts["ground_truth_fault_onset"]
    )
    assert truth
    record = orjson.loads(truth[0].value())
    assert record["type"] == "FAULT_ONSET"
    assert record["failure_mode"]

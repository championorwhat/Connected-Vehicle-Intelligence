"""Normalizer service against a real Kafka broker.

simulator -> telemetry.raw -> NormalizerService -> telemetry.canonical / telemetry.dlq

Checks: every raw message is accounted for (canonical + dlq + dropped duplicates),
no event_id appears twice on the canonical topic, DLQ records carry the reason and
source offset, and committed offsets mean a restarted consumer re-reads nothing.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import orjson
from confluent_kafka import Consumer

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.publishers import KafkaPublisher
from prognos_stream.processor import Normalizer
from prognos_stream.registry import VehicleRegistry
from prognos_stream.service import NormalizerService, ServiceConfig

START = time.time() - 60  # recent, so no event is rejected as stale


def _drain(bootstrap: str, topic: str) -> list[Any]:
    consumer = Consumer(
        {"bootstrap.servers": bootstrap, "group.id": f"drain-{topic}-{time.time()}",
         "auto.offset.reset": "earliest", "enable.auto.commit": False}
    )  # fmt: skip
    consumer.subscribe([topic])
    messages: list[Any] = []
    idle_since = time.monotonic()
    while time.monotonic() - idle_since < 5:
        msg = consumer.poll(0.5)
        if msg is not None and msg.error() is None:
            messages.append(msg)
            idle_since = time.monotonic()
    consumer.close()
    return messages


def test_normalizer_end_to_end(kafka_bootstrap: str, fresh_topics) -> None:  # type: ignore[no-untyped-def]
    topics = fresh_topics("telemetry.raw", "telemetry.canonical", "telemetry.dlq", "sim.truth")
    roster = generate_roster(400, 4, 42)
    cfg = SimConfig(
        vehicle_count=400, events_per_second=400.0, tenant_count=4, mode=Mode.FAST,
        publisher=PublisherKind.KAFKA, kafka_bootstrap_servers=kafka_bootstrap,
        duplicate_rate=0.03, out_of_order_rate=0.05, malformed_rate=0.02,
        missing_field_rate=0.02, metrics_port=0, raw_topic=topics["telemetry.raw"],
        ground_truth_topic=topics["sim.truth"],
    )  # fmt: skip
    sim = ShardSimulator(cfg, 0, roster.vehicles, KafkaPublisher(kafka_bootstrap, "it"), START)
    for tick in range(150):  # 15 simulated seconds
        sim.tick(START + tick * 0.1, 0.1)
    sim.finish()
    raw_sent = sim.counts["events_generated"] + sim.counts["duplicates"]

    service_cfg = ServiceConfig(
        bootstrap_servers=kafka_bootstrap, group_id="normalizer-it", batch_size=500,
        exit_when_idle_s=5, lag_interval_s=1, raw_topic=topics["telemetry.raw"],
        canonical_topic=topics["telemetry.canonical"], dlq_topic=topics["telemetry.dlq"],
    )  # fmt: skip
    normalizer = Normalizer(VehicleRegistry.from_roster(roster))
    stats = NormalizerService(service_cfg, normalizer).run()
    assert stats["consumed"] == raw_sent

    counts = normalizer.counts
    assert counts["canonical"] + counts["dlq"] + counts["duplicate"] == raw_sent

    canonical = _drain(kafka_bootstrap, topics["telemetry.canonical"])
    dlq = _drain(kafka_bootstrap, topics["telemetry.dlq"])
    assert len(canonical) == counts["canonical"]
    assert len(dlq) == counts["dlq"]

    ids = [orjson.loads(m.value())["event_id"] for m in canonical]
    assert len(ids) == len(set(ids))
    vehicle_partitions: dict[bytes, set[int]] = {}
    for m in canonical:
        vehicle_partitions.setdefault(m.key(), set()).add(m.partition())
    assert all(len(p) == 1 for p in vehicle_partitions.values())  # keyed by vehicle_id

    reasons = Counter(dict(m.headers())["dlq.reason"].decode() for m in dlq)
    assert reasons["malformed_json"] > 0
    source = f"{topics['telemetry.raw']}/".encode()
    assert all(dict(m.headers())["dlq.source"].startswith(source) for m in dlq)

    # Offsets were committed: a restart in the same group finds nothing to re-process.
    again = Normalizer(VehicleRegistry.from_roster(roster))
    restart = NormalizerService(service_cfg, again).run()
    assert restart["consumed"] == 0

"""Detector service against a real broker: canonical in -> alerts + vehicle.state out."""

from __future__ import annotations

import time
from typing import Any

import orjson
from confluent_kafka import Consumer, Producer

from prognos_stream.detector_service import DetectorConfig, DetectorService

T0 = time.time() - 120


def _iso(ts: float) -> str:
    sec = int(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec)) + f".{int((ts - sec) * 1000):03d}Z"


def _event(vehicle: str, seq: int, ts: float, coolant: float) -> bytes:
    return orjson.dumps(
        {
            "vehicle_id": vehicle, "tenant_id": "t-1", "vin": "PG1CT1A59RC000001", "seq": seq,
            "event_ts": _iso(ts), "event_type": "PERIODIC", "speed_kmh": 40.0,
            "ignition_on": True, "coolant_temp_c": coolant, "engine_rpm": 1900,
            "lv_battery_v": 14.0, "tyre_fl_kpa": 240.0, "tyre_fr_kpa": 240.0,
            "tyre_rl_kpa": 240.0, "tyre_rr_kpa": 240.0, "dtc_codes": [],
        }
    )  # fmt: skip


def _drain(bootstrap: str, topic: str) -> list[Any]:
    consumer = Consumer(
        {"bootstrap.servers": bootstrap, "group.id": f"drain-{topic}-{time.time()}",
         "auto.offset.reset": "earliest", "enable.auto.commit": False}
    )  # fmt: skip
    consumer.subscribe([topic])
    messages: list[Any] = []
    idle = time.monotonic()
    while time.monotonic() - idle < 5:
        msg = consumer.poll(0.5)
        if msg is not None and msg.error() is None:
            messages.append(msg)
            idle = time.monotonic()
    consumer.close()
    return messages


def test_detector_raises_and_clears_alerts(kafka_bootstrap: str, fresh_topics) -> None:  # type: ignore[no-untyped-def]
    topics = fresh_topics("telemetry.canonical", "alerts", "vehicle.state")
    producer = Producer({"bootstrap.servers": kafka_bootstrap})
    for vehicle in ("v-hot", "v-ok"):
        for seq in range(60):
            coolant = 118.0 if vehicle == "v-hot" and 20 <= seq < 30 else 90.0
            producer.produce(topics["telemetry.canonical"], _event(vehicle, seq, T0 + seq, coolant),
                             key=vehicle.encode())  # fmt: skip
    producer.flush(30)

    cfg = DetectorConfig(
        bootstrap_servers=kafka_bootstrap, group_id="detector-it", batch_size=100,
        exit_when_idle_s=5, input_topic=topics["telemetry.canonical"],
        alerts_topic=topics["alerts"], state_topic=topics["vehicle.state"], state_interval_s=15,
    )  # fmt: skip
    stats = DetectorService(cfg).run()
    assert stats["consumed"] == 120

    alerts = [orjson.loads(m.value()) for m in _drain(kafka_bootstrap, topics["alerts"])]
    transitions = [(a["vehicle_id"], a["rule_code"], a["status"]) for a in alerts]
    assert transitions == [
        ("v-hot", "COOLANT_OVERHEAT", "open"),
        ("v-hot", "COOLANT_OVERHEAT", "cleared"),
    ]
    assert alerts[0]["fingerprint"] == alerts[1]["fingerprint"]

    states = _drain(kafka_bootstrap, topics["vehicle.state"])
    assert {m.key() for m in states} == {b"v-hot", b"v-ok"}  # keyed for compaction

    # Replay from the beginning in a new group: identical fingerprints (idempotent sinks).
    replay_cfg = DetectorConfig(**{**cfg.__dict__, "group_id": "detector-replay"})
    DetectorService(replay_cfg).run()
    replayed = [orjson.loads(m.value()) for m in _drain(kafka_bootstrap, topics["alerts"])]
    assert {a["fingerprint"] for a in replayed} == {alerts[0]["fingerprint"]}

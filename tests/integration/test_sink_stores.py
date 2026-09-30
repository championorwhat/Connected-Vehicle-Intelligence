"""Sink service against real Kafka, PostgreSQL (seeded) and Redis."""

from __future__ import annotations

import threading
import time

import orjson
import psycopg
import redis
from confluent_kafka import Producer

from prognos_stream.sink_main import SinkConfig, SinkService
from prognos_stream.sinks import AlertStore, LiveStateStore


def _iso(ts: float) -> str:
    sec = int(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec)) + f".{int((ts - sec) * 1000):03d}Z"


def _alert(tenant: str, vehicle: str, status: str, fp: str, at: float) -> bytes:
    return orjson.dumps({
        "schema_version": 1, "fingerprint": fp, "status": status, "tenant_id": tenant,
        "vehicle_id": vehicle, "vin": "PG1CT1A59RC000001", "rule_code": "COOLANT_OVERHEAT",
        "severity": "critical", "failure_mode": "COOLING_FAILURE", "event_ts": _iso(at - 1),
        "event_seq": 1, "detected_at": _iso(at), "value": 115.0, "threshold": 112.0, "details": {},
    })  # fmt: skip


def test_sink_is_idempotent_and_isolates_bad_rows(  # type: ignore[no-untyped-def]
    kafka_bootstrap: str, fresh_topics, seeded_dsn: str, redis_url
) -> None:
    topics = fresh_topics("alerts", "vehicle.state")
    with psycopg.connect(seeded_dsn) as conn:
        tenant, vehicle = conn.execute(
            "SELECT tenant_id::text, vehicle_id::text FROM vehicles ORDER BY vehicle_id LIMIT 1"
        ).fetchone()  # type: ignore[misc]
    now = time.time()
    fp = "a" * 32
    producer = Producer({"bootstrap.servers": kafka_bootstrap})
    for payload in (
        _alert(tenant, vehicle, "open", fp, now),
        _alert(tenant, vehicle, "open", fp, now),  # redelivery of the same open
        _alert(tenant, vehicle, "cleared", fp, now + 30),
        _alert(
            tenant, "00000000-0000-4000-8000-000000000000", "open", "b" * 32, now
        ),  # unknown vehicle
    ):
        producer.produce(topics["alerts"], payload, key=vehicle.encode())
    state = {
        "vehicle_id": vehicle, "tenant_id": tenant, "health_score": 60,
        "latitude": 13.08, "longitude": 80.27, "active_alerts": ["COOLANT_OVERHEAT"],
    }  # fmt: skip
    producer.produce(topics["vehicle.state"], orjson.dumps(state), key=vehicle.encode())
    producer.flush(30)

    host, port = redis_url
    client = redis.Redis(host=host, port=port)
    pubsub = client.pubsub()  # type: ignore[no-untyped-call]
    pubsub.subscribe(f"alerts:{tenant}")
    received: list[bytes] = []

    def listen() -> None:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and len(received) < 3:
            message = pubsub.get_message(timeout=1.0)
            if message and message["type"] == "message":
                received.append(message["data"])

    listener = threading.Thread(target=listen)
    listener.start()

    cfg = SinkConfig(
        bootstrap_servers=kafka_bootstrap, group_id="sink-it", batch_size=100, exit_when_idle_s=5,
        input_topic=f"{topics['alerts']},{topics['vehicle.state']}",
        alerts_topic=topics["alerts"], state_topic=topics["vehicle.state"],
    )  # fmt: skip
    store = AlertStore(seeded_dsn)
    stats = SinkService(cfg, store, LiveStateStore(client)).run()
    listener.join()
    assert stats["consumed"] == 5
    assert store.stats["rejected"] == 1  # the unknown vehicle, isolated from the rest

    with psycopg.connect(seeded_dsn) as conn:
        rows = conn.execute(
            "SELECT status, resolved_at IS NOT NULL FROM alerts WHERE fingerprint = %s", (fp,)
        ).fetchall()
    assert rows == [("resolved", True)]  # one row despite the duplicate open

    live = orjson.loads(client.get(f"veh:{vehicle}"))  # type: ignore[arg-type]
    assert live["health_score"] == 60
    assert client.ttl(f"veh:{vehicle}") > 0
    assert client.zscore(f"tenant:{tenant}:health", vehicle) == 60
    assert client.geopos(f"tenant:{tenant}:geo", vehicle)[0] is not None
    assert len(received) == 3  # the three alert transitions for this tenant were published

    # Replaying the same topics in a new group changes nothing in PostgreSQL.
    replay = SinkConfig(**{**cfg.__dict__, "group_id": "sink-replay"})
    SinkService(replay, AlertStore(seeded_dsn), LiveStateStore(client)).run()
    with psycopg.connect(seeded_dsn) as conn:
        count = conn.execute("SELECT count(*) FROM alerts WHERE fingerprint = %s", (fp,)).fetchone()
    assert count == (1,)

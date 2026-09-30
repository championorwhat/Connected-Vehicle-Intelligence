"""End-to-end no-data-loss reconciliation across Kafka, ClickHouse and PostgreSQL.

    uv run python scripts/reconcile.py --output evidence/benchmarks/m6-reconciliation.json

Waits until the ClickHouse and sink consumer groups have caught up, then checks:
  1. Kafka:       raw = canonical + dlq + duplicates dropped (duplicates >= 0)
  2. ClickHouse:  unique event_ids in `events` == messages on telemetry.canonical
                  (the canonical topic never carries a repeated event_id, see M4 evidence)
                  and no rows in `ingestion_errors`
  3. PostgreSQL:  alert rows == unique opened fingerprints on the `alerts` topic
Exits non-zero if any check fails, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import clickhouse_connect
import orjson
import psycopg
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient


def topic_total(consumer: Consumer, topic: str) -> int:
    metadata = consumer.list_topics(topic, timeout=10)
    total = 0
    for partition in metadata.topics[topic].partitions:
        low, high = consumer.get_watermark_offsets(TopicPartition(topic, partition), timeout=10)
        total += high - low
    return total


def group_lag(admin: AdminClient, consumer: Consumer, group: str, topic: str) -> int | None:
    from confluent_kafka import ConsumerGroupTopicPartitions

    metadata = consumer.list_topics(topic, timeout=10)
    partitions = [TopicPartition(topic, p) for p in metadata.topics[topic].partitions]
    future = admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(group, partitions)])
    try:
        committed = future[group].result(timeout=10).topic_partitions
    except Exception:
        return None
    lag = 0
    for tp in committed:
        _, high = consumer.get_watermark_offsets(TopicPartition(topic, tp.partition), timeout=10)
        lag += high - max(tp.offset, 0)
    return lag


def opened_fingerprints(bootstrap: str) -> set[tuple[str, str]]:
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": f"reconcile-{time.time()}",
                         "auto.offset.reset": "earliest", "enable.auto.commit": False})  # fmt: skip
    consumer.subscribe(["alerts"])
    seen: set[tuple[str, str]] = set()
    idle = time.monotonic()
    while time.monotonic() - idle < 6:
        for msg in consumer.consume(5000, 1.0):
            if msg.error():
                continue
            idle = time.monotonic()
            alert = orjson.loads(msg.value() or b"{}")
            if alert["status"] == "open":
                seen.add((alert["tenant_id"], alert["fingerprint"]))
    consumer.close()
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bootstrap", default=os.getenv("KAFKA_HOST_BOOTSTRAP", "localhost:9092"))
    parser.add_argument("--wait-seconds", type=float, default=180.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    env = os.environ

    admin = AdminClient({"bootstrap.servers": args.bootstrap})
    consumer = Consumer({"bootstrap.servers": args.bootstrap, "group.id": "reconcile-meta"})
    deadline = time.monotonic() + args.wait_seconds
    lags: dict[str, int | None] = {}
    while True:
        lags = {
            "clickhouse": group_lag(admin, consumer, "clickhouse", "telemetry.canonical"),
            "sink": group_lag(admin, consumer, "sink", "alerts"),
        }
        if all(v == 0 for v in lags.values()) or time.monotonic() > deadline:
            break
        time.sleep(3)

    raw = topic_total(consumer, "telemetry.raw")
    canonical = topic_total(consumer, "telemetry.canonical")
    dlq = topic_total(consumer, "telemetry.dlq")
    consumer.close()

    ch = clickhouse_connect.get_client(
        host=env.get("CLICKHOUSE_HOST_LOCAL", "localhost"),
        port=int(env.get("CLICKHOUSE_HTTP_HOST_PORT", "8123")),
        username=env.get("CLICKHOUSE_USER", "prognos"),
        password=env["CLICKHOUSE_PASSWORD"],
        database=env.get("CLICKHOUSE_DB", "telemetry"),
    )
    ch_rows, ch_unique = ch.query("SELECT count(), uniqExact(event_id) FROM events").result_rows[0]
    ch_errors = ch.query("SELECT count() FROM ingestion_errors").result_rows[0][0]

    dsn = (f"host=localhost port={env.get('POSTGRES_HOST_PORT', '5432')} "
           f"dbname={env.get('POSTGRES_DB', 'prognos')} user={env.get('POSTGRES_USER', 'prognos')} "
           f"password={env['POSTGRES_PASSWORD']}")  # fmt: skip
    with psycopg.connect(dsn) as conn:
        pg_alerts = conn.execute("SELECT count(*) FROM alerts").fetchone()[0]  # type: ignore[index]
    opened = opened_fingerprints(args.bootstrap)

    checks: dict[str, Any] = {
        # Guard against a vacuous pass: every equality below holds trivially at zero.
        "pipeline_carried_events": canonical > 0 and ch_unique > 0,
        "consumers_caught_up": all(v == 0 for v in lags.values()),
        "kafka_balances": raw - canonical - dlq >= 0,
        "clickhouse_has_every_canonical_event": ch_unique == canonical,
        "clickhouse_no_ingestion_errors": ch_errors == 0,
        "postgres_has_every_opened_alert": pg_alerts == len(opened),
    }
    result = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "consumer_lag": lags,
        "kafka": {
            "telemetry.raw": raw,
            "telemetry.canonical": canonical,
            "telemetry.dlq": dlq,
            "duplicates_dropped_by_normalizer": raw - canonical - dlq,
        },
        "clickhouse": {
            "rows": ch_rows,
            "unique_event_ids": ch_unique,
            "rows_pending_replacing_merge": ch_rows - ch_unique,
            "ingestion_errors": ch_errors,
        },
        "postgres": {"alert_rows": pg_alerts, "unique_opened_fingerprints": len(opened)},
        "checks": checks,
        "passed": all(checks.values()),
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

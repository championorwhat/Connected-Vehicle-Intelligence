"""ClickHouse telemetry schema: dedup semantics, rollup correctness, migration idempotency."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.integration.conftest import CH_DIR, run_ch_migrations

CH_MIGRATIONS = CH_DIR / "migrations"

COLUMNS = [
    "event_id", "tenant_id", "vehicle_id", "vin", "oem", "schema_version", "seq", "event_ts",
    "ingest_ts", "event_type", "latitude", "longitude", "speed_kmh", "heading_deg", "odometer_km",
    "ignition_on", "engine_rpm", "coolant_temp_c", "lv_battery_v", "tyre_fl_kpa", "tyre_fr_kpa",
    "tyre_rl_kpa", "tyre_rr_kpa", "dtc_codes",
]  # fmt: skip


def event(
    tenant: uuid.UUID,
    vehicle: uuid.UUID,
    seq: int,
    ts: datetime,
    *,
    coolant: float = 90.0,
    tyre_fl: float = 240.0,
    dtcs: list[str] | None = None,
    ingest_delay_ms: int = 50,
) -> list[Any]:
    return [
        uuid.uuid4(), tenant, vehicle, "PG1CT1A59RC000001", "ORION", 1, seq, ts,
        ts + timedelta(milliseconds=ingest_delay_ms), "PERIODIC", 13.08, 80.27, 42.0, 90,
        1000.0 + seq, True, 2100, coolant, 12.6, tyre_fl, 238.0, 241.0, 239.0, dtcs or [],
    ]  # fmt: skip


def test_tables_exist(ch) -> None:  # type: ignore[no-untyped-def]
    tables = set(ch.query("SHOW TABLES").result_columns[0])
    assert {"events", "vehicle_minute", "vehicle_minute_mv", "risk_scores", "dlq_events"} <= tables
    assert {"canonical_queue", "canonical_to_events", "alert_history", "ingestion_errors"} <= tables


def test_replacing_merge_tree_collapses_replays(ch) -> None:  # type: ignore[no-untyped-def]
    tenant, vehicle = uuid.uuid4(), uuid.uuid4()
    t0 = datetime(2026, 9, 29, 10, 0, 0, tzinfo=UTC)
    ch.insert(
        "events",
        [event(tenant, vehicle, s, t0 + timedelta(seconds=s)) for s in range(10)],
        column_names=COLUMNS,
    )
    # At-least-once redelivery of seq 3 and 7 arrives in a *later* batch (a new part).
    # (Duplicates inside one insert block are already collapsed at write time.)
    ch.insert(
        "events",
        [event(tenant, vehicle, s, t0 + timedelta(seconds=s), ingest_delay_ms=900) for s in (3, 7)],
        column_names=COLUMNS,
    )
    params = {"v": vehicle}
    raw = ch.query("SELECT count() FROM events WHERE vehicle_id = %(v)s", params)
    exact = ch.query("SELECT count() FROM events FINAL WHERE vehicle_id = %(v)s", params)
    assert raw.result_rows[0][0] == 12  # duplicates physically present until a merge
    assert exact.result_rows[0][0] == 10  # FINAL gives exact, deduplicated results

    kept = ch.query(
        "SELECT dateDiff('millisecond', event_ts, ingest_ts) FROM events FINAL"
        " WHERE vehicle_id = %(v)s AND seq = 3",
        params,
    ).result_rows
    assert kept == [(900,)]  # version column ingest_ts: the latest copy wins

    ch.command("OPTIMIZE TABLE events FINAL")
    merged = ch.query("SELECT count() FROM events WHERE vehicle_id = %(v)s", params)
    assert merged.result_rows[0][0] == 10  # after a merge, replays are physically gone


def test_minute_rollup_is_duplicate_safe(ch) -> None:  # type: ignore[no-untyped-def]
    tenant, vehicle = uuid.uuid4(), uuid.uuid4()
    t0 = datetime(2026, 9, 29, 11, 0, 0, tzinfo=UTC)
    rows = [
        event(tenant, vehicle, s, t0 + timedelta(seconds=s), coolant=90 + s % 5, tyre_fl=240 - s)
        for s in range(60)
    ]
    rows[30] = event(tenant, vehicle, 30, t0 + timedelta(seconds=30), coolant=120.0, dtcs=["P0217"])
    ch.insert("events", rows, column_names=COLUMNS)
    ch.insert("events", rows[:20], column_names=COLUMNS)  # a duplicated batch

    result = ch.query(
        """SELECT uniqExactMerge(events), max(max_coolant_c), min(min_tyre_kpa),
                  groupUniqArrayArray(dtc_codes)
           FROM vehicle_minute WHERE vehicle_id = %(v)s GROUP BY vehicle_id""",
        {"v": vehicle},
    ).result_rows[0]
    events, max_coolant, min_tyre, dtcs = result
    assert events == 60  # uniqExact(seq) ignores the duplicated batch
    assert max_coolant == 120.0
    assert min_tyre == 181.0  # 240 - 59
    assert dtcs == ["P0217"]


def test_nullable_signals_for_evs(ch) -> None:  # type: ignore[no-untyped-def]
    tenant, vehicle = uuid.uuid4(), uuid.uuid4()
    row = event(tenant, vehicle, 1, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
    row[COLUMNS.index("engine_rpm")] = None
    row[COLUMNS.index("coolant_temp_c")] = None
    ch.insert("events", [row], column_names=COLUMNS)
    got = ch.query(
        "SELECT engine_rpm, coolant_temp_c FROM events WHERE vehicle_id = %(v)s", {"v": vehicle}
    ).result_rows[0]
    assert got == (None, None)


def test_migration_runner_is_idempotent(ch, ch_container) -> None:  # type: ignore[no-untyped-def]
    output = run_ch_migrations(ch_container)
    assert "Applying" not in output
    versions = ch.query("SELECT count() FROM schema_migrations FINAL").result_rows[0][0]
    migration_files = len(list((CH_MIGRATIONS).glob("*.sql")))
    assert versions == migration_files


def test_ignition_is_nullable_after_migration(ch) -> None:  # type: ignore[no-untyped-def]
    column_type = ch.query(
        "SELECT type FROM system.columns WHERE table = 'events' AND name = 'ignition_on'"
    ).result_rows[0][0]
    assert column_type == "Nullable(Bool)"

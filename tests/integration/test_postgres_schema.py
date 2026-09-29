"""PostgreSQL schema: constraints, tenant integrity, idempotency guards, audit immutability."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import errors

from tests.integration.conftest import apply_pg


def one(db: psycopg.Connection, sql: str, *params: object) -> tuple[Any, ...]:
    row = db.execute(sql, params or None).fetchone()  # type: ignore[arg-type,unused-ignore]
    assert row is not None
    return row


def two_tenants(db: psycopg.Connection) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    rows = db.execute(
        """SELECT DISTINCT ON (v.tenant_id)
                  v.tenant_id, v.vehicle_id, v.fleet_id, v.home_workshop_id
           FROM vehicles v ORDER BY v.tenant_id LIMIT 2"""
    ).fetchall()
    return rows[0], rows[1]


# --- seed integrity -----------------------------------------------------------


def test_seed_loaded(db: psycopg.Connection) -> None:
    assert one(db, "SELECT count(*) FROM vehicles")[0] == 2000
    assert one(db, "SELECT count(*) FROM tenants")[0] == 5
    assert one(db, "SELECT count(*) FROM roles")[0] == 5


def test_no_vehicle_points_at_another_tenants_fleet(db: psycopg.Connection) -> None:
    mismatches = one(
        db,
        "SELECT count(*) FROM vehicles v JOIN fleets f USING (fleet_id) "
        "WHERE f.tenant_id <> v.tenant_id",
    )[0]
    assert mismatches == 0


def test_cost_parameters_are_flagged_as_placeholders(db: psycopg.Connection) -> None:
    unflagged = one(db, "SELECT count(*) FROM cost_parameters WHERE source NOT LIKE 'PLACEHOLDER%'")
    assert unflagged[0] == 0


# --- constraints ---------------------------------------------------------------


@pytest.mark.parametrize("bad_vin", ["PG1CT1A50RC00000O", "SHORT", "pg1ct1a50rc000001"])
def test_vin_check_constraint(db: psycopg.Connection, bad_vin: str) -> None:
    tenant, *_ = two_tenants(db)
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO vehicles (tenant_id, fleet_id, vin, model_code, model_year,"
            " firmware_version, commissioned_on) VALUES (%s, %s, %s, 'CT1A5', 2024, '1', now())",
            (tenant[0], tenant[2], bad_vin),
        )


def test_vehicle_cannot_join_another_tenants_fleet(db: psycopg.Connection) -> None:
    a, b = two_tenants(db)
    with pytest.raises(errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO vehicles (tenant_id, fleet_id, vin, model_code, model_year,"
            " firmware_version, commissioned_on)"
            " VALUES (%s, %s, 'PG1CT1A59RC999999', 'CT1A5', 2024, '1', now())",
            (a[0], b[2]),  # tenant A, fleet of tenant B
        )


def test_work_order_cannot_use_another_tenants_workshop(db: psycopg.Connection) -> None:
    a, b = two_tenants(db)
    with pytest.raises(errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, failure_mode)"
            " VALUES (%s, %s, %s, 'TYRE_SLOW_LEAK')",
            (a[0], a[1], b[3]),
        )


def test_overlapping_subscriptions_rejected(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    with pytest.raises(errors.ExclusionViolation):
        db.execute(
            "INSERT INTO subscriptions (tenant_id, plan, vehicle_limit, valid_during)"
            " VALUES (%s, 'growth', 10, '[2026-06-01,2026-07-01)')",
            (tenant[0],),
        )


def test_vehicle_cannot_have_two_drivers_at_once(db: psycopg.Connection) -> None:
    tenant_id, vehicle_id = one(db, "SELECT tenant_id, vehicle_id FROM driver_assignments LIMIT 1")
    driver_id = one(
        db,
        "INSERT INTO drivers (tenant_id, pseudonym) VALUES (%s, 'DRV-999999') RETURNING driver_id",
        tenant_id,
    )[0]
    with pytest.raises(errors.ExclusionViolation):
        db.execute(
            "INSERT INTO driver_assignments (tenant_id, vehicle_id, driver_id, during)"
            " VALUES (%s, %s, %s, '[2026-09-01,)')",
            (tenant_id, vehicle_id, driver_id),
        )


def test_driver_pseudonym_format_enforced(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO drivers (tenant_id, pseudonym) VALUES (%s, 'Ravi Kumar')", (tenant[0],)
        )


def test_cost_parameters_require_provenance(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    db.execute("DELETE FROM cost_parameters WHERE tenant_id = %s", (tenant[0],))
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO cost_parameters VALUES (%s, 'TYRE_SLOW_LEAK', 1, 2, 3, 1, 'INR', '')",
            (tenant[0],),
        )


# --- idempotency guards ---------------------------------------------------------


def _insert_alert(db: psycopg.Connection, tenant_id: object, vehicle_id: object) -> None:
    db.execute(
        "INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity, event_ts)"
        " VALUES (%s, %s, 'fp-1', 'COOLANT_OVERHEAT', 'critical', now())"
        " ON CONFLICT (tenant_id, fingerprint) DO NOTHING",
        (tenant_id, vehicle_id),
    )


def test_alert_upsert_is_idempotent(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    for _ in range(3):  # simulates at-least-once redelivery of the same alert
        _insert_alert(db, tenant[0], tenant[1])
    assert one(db, "SELECT count(*) FROM alerts WHERE fingerprint = 'fp-1'")[0] == 1


def test_acknowledged_alert_needs_timestamp(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    _insert_alert(db, tenant[0], tenant[1])
    with pytest.raises(errors.CheckViolation):
        db.execute("UPDATE alerts SET status = 'acknowledged' WHERE fingerprint = 'fp-1'")


def test_one_active_work_order_per_vehicle_and_mode(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    insert = (
        "INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, failure_mode, status,"
        " completed_at, outcome) VALUES (%s, %s, %s, 'TYRE_SLOW_LEAK', %s, %s, %s)"
    )
    db.execute(insert, (tenant[0], tenant[1], tenant[3], "completed", "2099-01-01", "other"))
    db.execute(insert, (tenant[0], tenant[1], tenant[3], "proposed", None, None))
    with pytest.raises(errors.UniqueViolation):
        db.execute(insert, (tenant[0], tenant[1], tenant[3], "proposed", None, None))


def test_completed_work_order_needs_outcome(db: psycopg.Connection) -> None:
    tenant, _ = two_tenants(db)
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, failure_mode, status)"
            " VALUES (%s, %s, %s, 'TYRE_SLOW_LEAK', 'completed')",
            (tenant[0], tenant[1], tenant[3]),
        )


# --- audit log --------------------------------------------------------------------


def test_audit_log_is_partitioned_and_append_only(db: psycopg.Connection) -> None:
    partition = one(
        db,
        "INSERT INTO audit_log (actor_type, action, resource_type, outcome)"
        " VALUES ('system', 'vehicle.read', 'vehicle', 'success')"
        " RETURNING tableoid::regclass::text",
    )[0]
    assert partition.startswith("audit_log_2")  # monthly partition, not the default
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("UPDATE audit_log SET outcome = 'denied'")


def test_audit_log_delete_denied(db: psycopg.Connection) -> None:
    db.execute(
        "INSERT INTO audit_log (actor_type, action, resource_type, outcome)"
        " VALUES ('user', 'alert.ack', 'alert', 'success')"
    )
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("DELETE FROM audit_log")


# --- RBAC policy data ---------------------------------------------------------------


def _perms(db: psycopg.Connection, role: str) -> set[str]:
    rows = db.execute("SELECT permission FROM role_permissions WHERE role_code = %s", (role,))
    return {r[0] for r in rows.fetchall()}


def test_least_privilege_roles(db: psycopg.Connection) -> None:
    assert "telemetry:read" not in _perms(db, "platform_admin")
    assert "location:read_precise" not in _perms(db, "analyst")
    assert "location:read_precise" in _perms(db, "fleet_manager")
    assert "privacy:erase" in _perms(db, "dpo")
    assert "work_order:write" not in _perms(db, "technician")


# --- schema hygiene ---------------------------------------------------------------------

# An FK is "supported" when a non-partial index leads with the FK's first column.
# Composite tenant FKs lead with a unique id (vehicle_id, fleet_id, ...), so that
# column alone is selective enough for parent-side lookups.
UNINDEXED_FK_SQL = """
SELECT c.conrelid::regclass::text, c.conname
FROM pg_constraint c
WHERE c.contype = 'f'
  AND NOT EXISTS (
    SELECT 1 FROM pg_index i
    WHERE i.indrelid = c.conrelid
      AND i.indpred IS NULL
      AND (i.indkey::int2[])[0] = c.conkey[1]
  )
"""


def test_every_foreign_key_is_indexed(db: psycopg.Connection) -> None:
    missing = db.execute(UNINDEXED_FK_SQL).fetchall()
    assert missing == [], f"FKs without a supporting index: {missing}"


def test_migrations_roll_down_and_up_cleanly(pg_container) -> None:  # type: ignore[no-untyped-def]
    name = f"migtest_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_container.get_connection_url(), autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")  # type: ignore[arg-type,unused-ignore]
    dsn = pg_container.get_connection_url().rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(dsn) as conn:
        apply_pg(conn, "up")
        apply_pg(conn, "down")
        remaining = conn.execute(
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchone()
        assert remaining == (0,)
        apply_pg(conn, "up")
        assert conn.execute("SELECT count(*) FROM roles").fetchone() == (5,)

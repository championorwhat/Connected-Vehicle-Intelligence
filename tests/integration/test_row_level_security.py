"""Row-level security: the database itself keeps tenants apart (ADR-010).

Each test runs a query *without* a tenant filter, as the API's tenant role would if a
developer forgot `WHERE tenant_id = ...`, and checks that only the caller's rows appear.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from prognos_api.deps import TENANT_ROLE, reset_session


@pytest.fixture(scope="module")
def tenants(seeded_dsn: str) -> tuple[str, str]:
    with psycopg.connect(seeded_dsn) as conn:
        rows = conn.execute("SELECT tenant_id::text FROM tenants ORDER BY slug LIMIT 2").fetchall()
    return rows[0][0], rows[1][0]


@pytest.fixture
def as_tenant(seeded_dsn: str) -> Iterator[Any]:
    """Connection factory: `as_tenant(tid)` behaves like the API's tenant_db dependency."""
    conns: list[psycopg.Connection[Any]] = []

    def connect(tenant_id: str | None) -> psycopg.Connection[Any]:
        conn = psycopg.connect(seeded_dsn, autocommit=True)
        conn.execute("SELECT set_config('app.tenant_id', %s, false), set_config('role', %s, false)",
                     (tenant_id or "", TENANT_ROLE))  # fmt: skip
        conns.append(conn)
        return conn

    yield connect
    for conn in conns:
        conn.close()


def count(conn: psycopg.Connection[Any], sql: str, *args: Any) -> int:
    row = conn.execute(sql, args).fetchone()
    assert row is not None
    return int(row[0])


def test_unfiltered_queries_only_see_the_callers_tenant(
    seeded_dsn: str, tenants: tuple[str, str], as_tenant: Any
) -> None:
    a, b = tenants
    with psycopg.connect(seeded_dsn) as owner:
        expected = count(owner, "SELECT count(*) FROM vehicles WHERE tenant_id = %s", a)
        b_vehicle = owner.execute("SELECT vehicle_id FROM vehicles WHERE tenant_id = %s LIMIT 1",
                                  (b,)).fetchone()[0]  # type: ignore[index]  # fmt: skip
    conn = as_tenant(a)
    assert expected > 0
    for table in ("vehicles", "fleets", "workshops", "alerts", "work_orders", "users", "tenants"):
        others = count(conn, f"SELECT count(*) FROM {table} WHERE tenant_id <> %s", a)
        assert others == 0, table
    assert count(conn, "SELECT count(*) FROM vehicles") == expected
    assert count(conn, "SELECT count(*) FROM vehicles WHERE vehicle_id = %s", b_vehicle) == 0


def test_no_tenant_sees_nothing(as_tenant: Any) -> None:
    conn = as_tenant(None)  # e.g. platform staff, or a code path that forgot to set it
    assert count(conn, "SELECT count(*) FROM vehicles") == 0
    assert count(conn, "SELECT count(*) FROM tenants") == 0


def test_writes_into_another_tenant_are_rejected(
    seeded_dsn: str, tenants: tuple[str, str], as_tenant: Any
) -> None:
    a, b = tenants
    with psycopg.connect(seeded_dsn) as owner:
        vehicle, workshop = owner.execute(
            "SELECT v.vehicle_id, w.workshop_id FROM vehicles v"
            " JOIN workshops w USING (tenant_id) WHERE v.tenant_id = %s LIMIT 1", (b,),
        ).fetchone()  # type: ignore[misc]  # fmt: skip
    conn = as_tenant(a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        conn.execute(
            "INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, failure_mode, status)"
            " VALUES (%s, %s, %s, 'BRAKE_WEAR', 'proposed')", (b, vehicle, workshop),
        )  # fmt: skip
    # UPDATE of another tenant's rows silently matches nothing.
    assert conn.execute("UPDATE work_orders SET status = status WHERE tenant_id = %s",
                        (b,)).rowcount == 0  # fmt: skip


def test_tenant_role_cannot_read_password_hashes_or_rewrite_history(
    tenants: tuple[str, str], as_tenant: Any
) -> None:
    conn = as_tenant(tenants[0])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT password_hash FROM users")
    assert count(conn, "SELECT count(*) FROM users") >= 0  # other columns stay readable
    for sql in ("DELETE FROM audit_log", "DELETE FROM alerts", "TRUNCATE vehicles"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql)


def test_audit_entries_are_written_and_read_per_tenant(
    tenants: tuple[str, str], as_tenant: Any
) -> None:
    a, b = tenants
    conn = as_tenant(a)
    insert = ("INSERT INTO audit_log (tenant_id, actor_type, action, resource_type, outcome)"
              " VALUES (%s, 'user', 'rls.probe', 'test', 'success')")  # fmt: skip
    conn.execute(insert, (a,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        conn.execute(insert, (b,))  # cannot forge another tenant's audit trail
    assert count(conn, "SELECT count(*) FROM audit_log WHERE tenant_id <> %s", a) == 0


def test_pool_never_hands_out_a_connection_still_bound_to_a_tenant(
    seeded_dsn: str, tenants: tuple[str, str]
) -> None:
    async def run() -> tuple[str, str | None]:
        async with AsyncConnectionPool(seeded_dsn, min_size=1, max_size=1, open=False,
                                       kwargs={"autocommit": True},
                                       reset=reset_session) as pool:  # fmt: skip
            async with pool.connection() as conn:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', %s, false), set_config('role', %s, false)",
                    (tenants[0], TENANT_ROLE),
                )
            async with pool.connection() as conn:  # same physical connection (max_size=1)
                row = await (await conn.execute(
                    "SELECT current_user::text, current_setting('app.tenant_id', true)"
                )).fetchone()  # fmt: skip
                assert row is not None
                return row[0], row[1]

    user, tenant = asyncio.run(run())
    assert user != TENANT_ROLE
    assert not tenant

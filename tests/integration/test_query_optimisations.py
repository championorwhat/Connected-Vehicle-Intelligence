"""M15 rewrites must return exactly what the straightforward queries return.

The fleet summary was rewritten from eight count subqueries into one pass per table over
active rows only, and the work-order list's ORDER BY was qualified so an index can serve
it (docs/performance/sql-optimisation.md). Faster must not mean different.
"""

from __future__ import annotations

import itertools
from typing import Any

import psycopg
import pytest

from prognos_api import pagination
from prognos_api.routers.live import SUMMARY_SQL
from prognos_api.routers.work_orders import LIST_ORDER

NAIVE_SUMMARY = {
    "vehicles": "SELECT count(*) FROM vehicles WHERE tenant_id = %(t)s",
    "vehicles_in_workshop": "SELECT count(*) FROM vehicles WHERE tenant_id = %(t)s"
                            " AND status = 'in_workshop'",
    "open_critical_alerts": "SELECT count(*) FROM alerts WHERE tenant_id = %(t)s"
                            " AND status = 'open' AND severity = 'critical'",
    "open_warning_alerts": "SELECT count(*) FROM alerts WHERE tenant_id = %(t)s"
                           " AND status = 'open' AND severity = 'warning'",
    "acknowledged_alerts": "SELECT count(*) FROM alerts WHERE tenant_id = %(t)s"
                           " AND status = 'acknowledged'",
    "work_orders_proposed": "SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s"
                            " AND status = 'proposed'",
    "work_orders_scheduled": "SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s"
                             " AND status = 'scheduled'",
    "work_orders_in_progress": "SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s"
                               " AND status = 'in_progress'",
}  # fmt: skip


@pytest.fixture(scope="module")
def tenant(seeded_dsn: str) -> str:
    """Tenant A with alerts in every status x severity and work orders in every status."""
    with psycopg.connect(seeded_dsn) as conn:
        tid = conn.execute("SELECT tenant_id::text FROM tenants ORDER BY slug LIMIT 1").fetchone()[
            0
        ]  # type: ignore[index]
        vehicles = [r[0] for r in conn.execute(
            # vehicles no other test has touched: none has an active work order yet
            "SELECT vehicle_id FROM vehicles v WHERE tenant_id = %s AND status = 'active'"
            " AND NOT EXISTS (SELECT 1 FROM work_orders w WHERE w.vehicle_id = v.vehicle_id)"
            " ORDER BY vehicle_id DESC LIMIT 60", (tid,))]  # fmt: skip
        shop = conn.execute("SELECT workshop_id FROM workshops WHERE tenant_id = %s LIMIT 1",
                            (tid,)).fetchone()[0]  # type: ignore[index]  # fmt: skip
        conn.execute("UPDATE vehicles SET status = 'in_workshop' WHERE vehicle_id = %s",
                     (vehicles[0],))  # fmt: skip
        statuses = ["open", "acknowledged", "resolved", "suppressed"]
        for i, (status, severity) in enumerate(
            itertools.product(statuses, ["info", "warning", "critical"])
        ):
            for k in range(i % 3 + 1):  # different counts per combination
                conn.execute(
                    "INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity,"
                    " status, event_ts, acknowledged_at, resolved_at) VALUES (%s, %s, %s,"
                    " 'M15_TEST', %s, %s, now() - interval '1 hour',"
                    " CASE WHEN %s = 'acknowledged' THEN now() END,"
                    " CASE WHEN %s = 'resolved' THEN now() END)",
                    (tid, vehicles[i], f"m15-{i}-{k}", severity, status, status, status),
                )
        wo_statuses = ["proposed", "scheduled", "in_progress", "completed", "cancelled"]
        for i in range(55):  # > one page of 'proposed', to exercise the keyset cursor
            status = "proposed" if i < 23 else wo_statuses[i % 5]
            conn.execute(
                "INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, failure_mode,"
                " status, scheduled_for, created_at, completed_at, outcome) VALUES (%s, %s, %s,"
                " 'COOLING_FAILURE', %s, current_date, now() - make_interval(mins => %s),"
                " CASE WHEN %s = 'completed' THEN now() END,"
                " CASE WHEN %s = 'completed' THEN 'other' END)",
                (tid, vehicles[i], shop, status, i % 7, status, status),  # ties in created_at
            )
    return str(tid)


def test_summary_matches_the_straightforward_counts(seeded_dsn: str, tenant: str) -> None:
    with psycopg.connect(seeded_dsn) as conn:
        cur = conn.execute(SUMMARY_SQL, {"t": tenant})
        row = cur.fetchone()
        assert row is not None
        fast = dict(zip([c.name for c in cur.description or []], row, strict=True))
        slow = {k: conn.execute(q, {"t": tenant}).fetchone()[0]  # type: ignore[index]
                for k, q in NAIVE_SUMMARY.items()}  # fmt: skip
    assert fast == slow
    assert all(v > 0 for v in slow.values())  # every counter is exercised


def test_status_filtered_pages_return_every_row_once_in_order(seeded_dsn: str, tenant: str) -> None:
    columns = "work_order_id, created_at"
    with psycopg.connect(seeded_dsn) as conn:
        expected = [r[0] for r in conn.execute(
            f"SELECT work_order_id FROM work_orders WHERE tenant_id = %s AND status = 'proposed'"
            f" ORDER BY {LIST_ORDER}", (tenant,))]  # fmt: skip
        seen: list[Any] = []
        after: list[Any] | None = None
        while True:
            sql = (
                f"SELECT {columns} FROM work_orders WHERE tenant_id = %(t)s AND status = 'proposed'"
            )
            params: dict[str, Any] = {"t": tenant}
            if after:
                sql += " AND (created_at, work_order_id) < (%(ts)s::timestamptz, %(id)s::uuid)"
                params["ts"], params["id"] = after
            rows = conn.execute(sql + f" ORDER BY {LIST_ORDER} LIMIT 11", params).fetchall()
            page = pagination.page([{"work_order_id": r[0], "created_at": r[1]} for r in rows],
                                   10, ["created_at", "work_order_id"])  # fmt: skip
            seen += [item["work_order_id"] for item in page["items"]]
            if not page["next_cursor"]:
                break
            after = pagination.decode(page["next_cursor"], 2)
    assert len(expected) >= 23
    assert seen == expected  # every row once, same order, across equal created_at values

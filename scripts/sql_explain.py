"""EXPLAIN ANALYZE the three slowest queries (M15), before or after the optimisation.

    uv run python scripts/sql_explain.py --label before --sql before
    uv run python scripts/sql_explain.py --label after  --sql after

The three were chosen by measurement (scripts/sql_workload.py, pg_stat_statements).
Each query runs exactly as in production: API queries as the RLS tenant role with
`app.tenant_id` set, the planner query as the owner role. It runs for every tenant
(API queries) or 5 times (planner). The median warm execution time is reported, plus
the full plan of one run. Output: evidence/performance/m15-<label>.json and
m15-<label>-plans.txt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as pgsql
from psycopg.conninfo import make_conninfo

OUT = Path(__file__).resolve().parents[1] / "evidence/performance"

# Q1: dashboard headline counts (apps/api/.../routers/live.py, fleet_summary)
SUMMARY_BEFORE = """
SELECT
  (SELECT count(*) FROM vehicles WHERE tenant_id = %(t)s) AS vehicles,
  (SELECT count(*) FROM vehicles WHERE tenant_id = %(t)s AND status = 'in_workshop')
      AS vehicles_in_workshop,
  (SELECT count(*) FROM alerts WHERE tenant_id = %(t)s AND status = 'open'
      AND severity = 'critical') AS open_critical_alerts,
  (SELECT count(*) FROM alerts WHERE tenant_id = %(t)s AND status = 'open'
      AND severity = 'warning') AS open_warning_alerts,
  (SELECT count(*) FROM alerts WHERE tenant_id = %(t)s AND status = 'acknowledged')
      AS acknowledged_alerts,
  (SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s AND status = 'proposed')
      AS work_orders_proposed,
  (SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s AND status = 'scheduled')
      AS work_orders_scheduled,
  (SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s AND status = 'in_progress')
      AS work_orders_in_progress
"""

# Q2: planner input (apps/stream-processor/.../planner_main.py, _ALERTS) - unchanged SQL
PLANNER = """
SELECT a.alert_id, a.tenant_id::text, a.vehicle_id::text, a.failure_mode, a.rule_code,
       a.severity, extract(epoch FROM now() - a.event_ts) / 3600.0,
       (a.details ->> 'estimated_hours_to_critical')::float8,
       hw.latitude::float8, hw.longitude::float8
FROM alerts a
JOIN vehicles v ON v.vehicle_id = a.vehicle_id
LEFT JOIN workshops hw ON hw.workshop_id = v.home_workshop_id
WHERE a.status IN ('open', 'acknowledged')
  AND a.failure_mode IS NOT NULL
  AND v.status = 'active'
  AND NOT EXISTS (
      SELECT 1 FROM work_orders w
      WHERE w.vehicle_id = a.vehicle_id AND w.failure_mode = a.failure_mode
        AND w.status IN ('proposed', 'scheduled', 'in_progress'))
"""

# Q3: work-order list filtered by status, last page (routers/work_orders.py)
WO_PAGE = """
SELECT work_order_id::text AS work_order_id, vehicle_id::text AS vehicle_id,
    workshop_id::text AS workshop_id, source_alert_id, failure_mode, status,
    failure_probability::float8 AS failure_probability,
    expected_cost_avoided::float8 AS expected_cost_avoided, model_version, scheduled_for,
    created_by::text AS created_by, created_at, completed_at, outcome
FROM work_orders WHERE tenant_id = %(t)s AND status = %(s)s
  AND (created_at, work_order_id) < (%(ts)s::timestamptz, %(id)s::uuid)
ORDER BY {order} LIMIT 51
"""
WO_BEFORE = WO_PAGE.replace("{order}", "created_at DESC, work_order_id DESC")


def after_sql() -> tuple[str, str]:
    """The optimised SQL, imported from the code that runs it."""
    from prognos_api.routers import live, work_orders

    return live.SUMMARY_SQL, WO_PAGE.replace("{order}", work_orders.LIST_ORDER)


def explain(conn: psycopg.Connection[Any], sql: str, params: dict[str, Any],
            mode: str) -> tuple[float, str]:  # fmt: skip
    """EXPLAIN ANALYZE a prepared statement, as the API runs it (psycopg prepares a query
    after 5 executions; PostgreSQL may then use a generic plan that cannot see the values).
    """
    names = list(dict.fromkeys(re.findall(r"%\((\w+)\)s", sql)))
    positional = re.sub(r"%\((\w+)\)s", lambda m: f"${names.index(m.group(1)) + 1}", sql)
    conn.execute("DEALLOCATE ALL")
    conn.execute(f"SET plan_cache_mode = {mode}")
    conn.execute(f"PREPARE q AS {positional}")
    args = pgsql.SQL(", ").join(pgsql.Literal(params[n]) for n in names)
    call = pgsql.SQL("EXECUTE q({})").format(args) if names else pgsql.SQL("EXECUTE q")
    head = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
    plan = conn.execute(pgsql.SQL(head).format() + call).fetchone()
    assert plan is not None
    text = conn.execute(pgsql.SQL("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) ") + call).fetchall()
    conn.execute("RESET plan_cache_mode")
    return float(plan[0][0]["Execution Time"]), "\n".join(r[0] for r in text)


def as_tenant(conn: psycopg.Connection[Any], tenant: str | None) -> None:
    if tenant is None:
        conn.execute("RESET ROLE; RESET app.tenant_id")
    else:
        conn.execute("SELECT set_config('app.tenant_id', %s, false),"
                     " set_config('role', 'prognos_tenant', false)", (tenant,))  # fmt: skip


def dsn() -> str:
    e = os.environ
    return make_conninfo(
        host=e.get("POSTGRES_SEED_HOST", "localhost"),
        port=e.get("POSTGRES_HOST_PORT", "5432"),
        dbname=e.get("POSTGRES_DB", "prognos"),
        user=e.get("POSTGRES_USER", "prognos"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--label", required=True)
    p.add_argument("--sql", choices=["before", "after"], required=True)
    args = p.parse_args()
    summary_sql, wo_sql = (SUMMARY_BEFORE, WO_BEFORE) if args.sql == "before" else after_sql()

    results: dict[str, Any] = {}
    plans: list[str] = []
    # prepare_threshold=None: psycopg must not prepare statements of its own here.
    with psycopg.connect(dsn(), autocommit=True, prepare_threshold=None) as conn:
        tenants = [r[0] for r in conn.execute("SELECT tenant_id::text FROM tenants ORDER BY slug")]
        sizes: dict[str, int] = dict(conn.execute(
            "SELECT relname, n_live_tup FROM pg_stat_user_tables"
            " WHERE relname IN ('vehicles', 'alerts', 'work_orders')").fetchall())  # fmt: skip
        indexes = [r[0] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename IN ('alerts', 'work_orders',"
            " 'vehicles') ORDER BY 1")]  # fmt: skip

        def run(name: str, sql: str, cases: list[tuple[str | None, dict[str, Any]]]) -> None:
            out: dict[str, Any] = {"runs": len(cases)}
            for mode, key in (("force_custom_plan", "custom"), ("force_generic_plan", "generic")):
                first: list[float] = []
                warm: list[float] = []
                plan = ""
                for tenant, params in cases:
                    as_tenant(conn, tenant)
                    first.append(explain(conn, sql, params, mode)[0])  # may read from disk
                    ms, plan = explain(conn, sql, params, mode)
                    warm.append(ms)
                as_tenant(conn, None)
                out[key] = {"median_ms": round(statistics.median(warm), 2),
                            "max_ms": round(max(warm), 2),
                            "median_first_run_ms": round(statistics.median(first), 2)}  # fmt: skip
                plans.append(f"===== {name}, {key} plan ({args.label}) =====\n"
                             f"{sql.strip()}\n\n{plan}\n")  # fmt: skip
            results[name] = out

        run("q1_fleet_summary", summary_sql, [(t, {"t": t}) for t in tenants])
        run("q2_planner_open_alerts", PLANNER, [(None, {})] * 5)
        cases = []
        for t in tenants:
            # The last page: 20 'proposed' orders remain after the cursor, so the query
            # must also prove there is no 21st (the worst case for a status filter).
            row = conn.execute(
                "SELECT created_at, work_order_id FROM work_orders WHERE tenant_id = %(t)s"
                " AND status = 'proposed' ORDER BY created_at DESC, work_order_id DESC"
                " OFFSET greatest((SELECT count(*) FROM work_orders WHERE tenant_id = %(t)s"
                " AND status = 'proposed') - 21, 0) LIMIT 1", {"t": t}).fetchone()  # fmt: skip
            if row:
                cases.append((t, {"t": t, "s": "proposed", "ts": row[0], "id": row[1]}))
        run("q3_work_orders_by_status_last_page", wo_sql, cases)

    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"label": args.label, "sql": args.sql, "table_rows": sizes, "indexes": indexes,
           "hardware": "x86_64 Linux container, 4 vCPU, PostgreSQL 17 in Docker; not the "
                       "target Mac", "results": results}  # fmt: skip
    (OUT / f"m15-{args.label}.json").write_text(json.dumps(doc, indent=2) + "\n")
    (OUT / f"m15-{args.label}-plans.txt").write_text("\n".join(plans))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

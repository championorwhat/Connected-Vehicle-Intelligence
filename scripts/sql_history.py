"""Add a year of operational history to a seeded database, for query measurements (M15).

    uv run python scripts/sql_history.py [--alerts 2000000] [--work-orders 150000]

The seed (database/postgres/seeds) creates the fleet but no history, so every query is
fast on it. A production fleet of 100K vehicles accumulates alerts and work orders; this
script generates that volume deterministically (setseed) in plain SQL:

- alerts: spread over 365 days, ~1 % open and ~0.5 % acknowledged (all recent), ~0.5 %
  suppressed, the rest resolved. Rule codes, severities and failure modes follow the
  detector's rules (apps/stream-processor/.../detector.py).
- work orders: ~2 % active (proposed / scheduled / in_progress, one per vehicle and
  failure mode, as the partial unique index requires), the rest completed or cancelled,
  each at a workshop of the vehicle's own tenant.

Rows it adds are tagged (fingerprint 'hist-...', model_version 'history') so
`--remove` deletes exactly them.
"""

from __future__ import annotations

import argparse
import os
import time

import psycopg
from psycopg.conninfo import make_conninfo

ALERTS = """
WITH v AS (SELECT array_agg(vehicle_id ORDER BY vehicle_id) AS ids,
                  array_agg(tenant_id ORDER BY vehicle_id) AS tids FROM vehicles),
     r AS (SELECT array_agg(rule_code) AS codes, array_agg(fm) AS fms, array_agg(sev) AS sevs
           FROM (VALUES ('COOLANT_OVERHEAT', 'COOLING_FAILURE', 'critical'),
                 ('TYRE_PRESSURE_CRITICAL', 'TYRE_SLOW_LEAK', 'critical'),
                 ('HV_CELL_CRITICAL', 'HV_BATTERY_THERMAL', 'critical'),
                 ('LV_BATTERY_CRITICAL', 'LV_BATTERY_FAILURE', 'critical'),
                 ('COOLANT_DRIFT', 'COOLING_FAILURE', 'warning'),
                 ('MISFIRE_ROUGHNESS', 'IGNITION_MISFIRE', 'warning'),
                 ('LV_BATTERY_WEAK', 'LV_BATTERY_FAILURE', 'warning'),
                 ('TYRE_SLOW_LEAK', 'TYRE_SLOW_LEAK', 'warning'),
                 ('HV_CELL_IMBALANCE', 'HV_BATTERY_THERMAL', 'warning'),
                 ('DTC_P0300', 'IGNITION_MISFIRE', 'info'))
           AS t (rule_code, fm, sev)),
     g AS (
        SELECT i, 1 + floor(random() * cardinality(v.ids))::int AS vi,
               1 + floor(random() * cardinality(r.codes))::int AS ri, random() AS s,
               random() AS age, random() AS dur
        FROM generate_series(1, %(n)s) AS i, v, r)
INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity, failure_mode,
                    status, event_ts, detected_at, acknowledged_at, resolved_at, details)
SELECT v.tids[g.vi], v.ids[g.vi], 'hist-' || g.i, r.codes[g.ri], r.sevs[g.ri], r.fms[g.ri],
       st.status,
       st.event_ts, st.event_ts + interval '1 second',
       CASE WHEN st.status = 'acknowledged' THEN st.event_ts + interval '10 minutes' END,
       CASE WHEN st.status = 'resolved'
            THEN st.event_ts + interval '1 second' + g.dur * interval '72 hours' END,
       '{}'::jsonb
FROM g, v, r,
LATERAL (SELECT CASE WHEN g.s < 0.010 THEN 'open' WHEN g.s < 0.015 THEN 'acknowledged'
                     WHEN g.s < 0.020 THEN 'suppressed' ELSE 'resolved' END AS status,
                -- active alerts are recent; history spans the year
                now() - CASE WHEN g.s < 0.015 THEN g.age * interval '7 days'
                             ELSE g.age * interval '365 days' END AS event_ts) AS st
"""

WORK_ORDERS = """
WITH v AS (SELECT array_agg(vehicle_id ORDER BY vehicle_id) AS ids,
                  array_agg(tenant_id ORDER BY vehicle_id) AS tids FROM vehicles),
     w AS (SELECT tenant_id, array_agg(workshop_id ORDER BY workshop_id) AS shops
           FROM workshops GROUP BY tenant_id),
     fm AS (SELECT array_agg(failure_mode ORDER BY failure_mode) AS modes FROM failure_modes),
     g AS (
        SELECT i,
               -- the first `active` rows use distinct vehicles, so (vehicle, mode) is unique
               CASE WHEN i <= %(active)s THEN i
                    ELSE 1 + floor(random() * cardinality(v.ids))::int END AS vi,
               1 + floor(random() * cardinality(fm.modes))::int AS fi,
               random() AS s, random() AS age, random() AS shop
        FROM generate_series(1, %(n)s) AS i, v, fm)
INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, failure_mode, status,
                         failure_probability, model_version, scheduled_for, created_at,
                         completed_at, outcome)
SELECT v.tids[g.vi], v.ids[g.vi],
       w.shops[1 + floor(g.shop * cardinality(w.shops))::int], fm.modes[g.fi], st.status,
       round((0.3 + 0.7 * g.s)::numeric, 4), 'history',
       CASE WHEN st.status IN ('scheduled', 'in_progress', 'proposed')
            THEN current_date + (g.age * 14)::int
            WHEN st.status = 'completed' THEN (st.created_at + interval '3 days')::date END,
       st.created_at,
       CASE WHEN st.status = 'completed' THEN st.created_at + interval '4 days' END,
       CASE WHEN st.status = 'completed'
            THEN (ARRAY['fault_confirmed', 'no_fault_found', 'other'])[1 + (g.i %% 3)] END
FROM g, v, fm, w,
LATERAL (SELECT CASE WHEN g.i > %(active)s THEN
                         CASE WHEN g.s < 0.8 THEN 'completed' ELSE 'cancelled' END
                     WHEN g.s < 0.5 THEN 'proposed' WHEN g.s < 0.85 THEN 'scheduled'
                     ELSE 'in_progress' END AS status,
                now() - CASE WHEN g.i <= %(active)s THEN g.age * interval '7 days'
                             ELSE interval '5 days' + g.age * interval '360 days'
                        END AS created_at) AS st
WHERE w.tenant_id = v.tids[g.vi]
"""


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
    p.add_argument("--alerts", type=int, default=2_000_000)
    p.add_argument("--work-orders", type=int, default=150_000)
    p.add_argument("--active-work-orders", type=int, default=3_000)
    p.add_argument("--seed", type=float, default=0.42)
    p.add_argument("--remove", action="store_true", help="delete previously generated history")
    args = p.parse_args()
    with psycopg.connect(dsn()) as conn:
        conn.execute("DELETE FROM work_orders WHERE model_version = 'history'")
        conn.execute("DELETE FROM alerts WHERE fingerprint LIKE 'hist-%'")
        if args.remove:
            print("history removed")
            return 0
        conn.execute("SELECT setseed(%s)", (args.seed,))
        started = time.perf_counter()
        alerts = conn.execute(ALERTS, {"n": args.alerts}).rowcount
        params = {"n": args.work_orders, "active": args.active_work_orders}
        orders = conn.execute(WORK_ORDERS, params).rowcount
        conn.commit()
        conn.autocommit = True
        conn.execute("VACUUM ANALYZE alerts")
        conn.execute("VACUUM ANALYZE work_orders")
    print(f"added {alerts} alerts and {orders} work orders in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

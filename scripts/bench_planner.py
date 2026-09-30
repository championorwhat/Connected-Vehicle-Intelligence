"""Planner cycle time on the seeded fleet with a synthetic backlog of open alerts.

    make up && ./scripts/seed.sh --vehicles 100000 --reset
    set -a; . ./.env; set +a
    uv run python scripts/bench_planner.py 5000 --output evidence/benchmarks/m7-planner-100k.json

DESTRUCTIVE on the local database: deletes all alerts and work orders first and after.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from pathlib import Path

import psycopg

from prognos_stream.planner import Calibration
from prognos_stream.planner_main import plan_once

# One alert per sampled vehicle, rule chosen by powertrain as the detector would raise it.
_BACKLOG = """
    INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity, failure_mode,
                        event_ts, details)
    SELECT v.tenant_id, v.vehicle_id, 'bench-' || v.vehicle_id, r.rule, r.sev, r.mode,
           now() - (random() * interval '6 hours'), '{}'::jsonb
    FROM (SELECT * FROM vehicles ORDER BY md5(vehicle_id::text) LIMIT %s) v
    JOIN vehicle_models m USING (model_code)
    CROSS JOIN LATERAL (
        SELECT * FROM (VALUES
            ('COOLANT_DRIFT', 'warning', 'COOLING_FAILURE', 'ICE'),
            ('COOLANT_OVERHEAT', 'critical', 'COOLING_FAILURE', 'HEV'),
            ('HV_CELL_IMBALANCE', 'warning', 'HV_BATTERY_THERMAL', 'BEV')
        ) AS x(rule, sev, mode, pt)
        WHERE x.pt = m.powertrain) r
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("alerts", type=int, nargs="?", default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    env = os.environ
    dsn = (
        f"host=localhost port={env.get('POSTGRES_HOST_PORT', '5432')} "
        f"dbname={env.get('POSTGRES_DB', 'prognos')} user={env.get('POSTGRES_USER', 'prognos')} "
        f"password={env['POSTGRES_PASSWORD']}"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM work_orders")
        conn.execute("DELETE FROM alerts")
        conn.execute(_BACKLOG, (args.alerts,))

        def one(sql: str) -> object:
            row = conn.execute(sql).fetchone()
            assert row is not None
            return row[0]

        cal = Calibration.load()
        t0 = time.perf_counter()
        first = plan_once(conn, cal)
        t1 = time.perf_counter()
        second = plan_once(conn, cal)
        t2 = time.perf_counter()
        result = {
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hardware": f"{platform.machine()} {platform.system()}, {os.cpu_count()} vCPU, "
            "PostgreSQL 17 in Docker (not the target Mac)",
            "fleet_vehicles": one("SELECT count(*) FROM vehicles"),
            "workshops": one("SELECT count(*) FROM workshops"),
            "workshop_slots_per_day": one("SELECT sum(daily_capacity) FROM workshops"),
            "synthetic_open_alerts": one("SELECT count(*) FROM alerts"),
            "note": "synthetic alerts (one per sampled vehicle, rule by powertrain); timing "
            "covers reading, scheduling and inserting work orders + audit rows in one "
            "transaction; Redis not used (home workshop locations)",
            "first_cycle": {k: v for k, v in first.items() if k != "top"}
            | {"wall_seconds": round(t1 - t0, 3)},
            "rerun_cycle": {k: v for k, v in second.items() if k != "top"}
            | {"wall_seconds": round(t2 - t1, 3)},
            "example_proposals": first["top"][:3],
        }
        conn.execute("DELETE FROM work_orders")
        conn.execute("DELETE FROM alerts WHERE fingerprint LIKE 'bench-%%'")
    text = json.dumps(result, indent=2, default=str)
    print(text)
    if args.output:
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

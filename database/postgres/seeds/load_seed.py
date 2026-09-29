"""Load reference data and the deterministic synthetic fleet into PostgreSQL.

    uv run python database/postgres/seeds/load_seed.py --vehicles 100000
    uv run python database/postgres/seeds/load_seed.py --vehicles 100000 --reset

Idempotent: reference data is upserted; fleet data is skipped if tenants already
exist (use --reset to truncate and reload). Bulk rows use COPY, which is roughly
an order of magnitude faster than batched INSERTs for 100K+ rows.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from prognos_common.catalog import (
    DEFAULT_COSTS,
    DTCS,
    FAILURE_MODES,
    OEMS,
    VEHICLE_MODELS,
)
from prognos_common.roster import Roster, generate_roster

TENANT_TABLES = (
    "tenants, subscriptions, fleets, workshops, vehicles, drivers, driver_assignments, "
    "cost_parameters, alerts, work_orders, erasure_requests, users, user_roles"
)


def dsn_from_env() -> str:
    return (
        f"host={os.getenv('POSTGRES_SEED_HOST', 'localhost')} "
        f"port={os.getenv('POSTGRES_HOST_PORT', '5432')} "
        f"dbname={os.getenv('POSTGRES_DB', 'prognos')} "
        f"user={os.getenv('POSTGRES_USER', 'prognos')} "
        f"password={os.environ['POSTGRES_PASSWORD']}"
    )


def upsert_reference_data(cur: psycopg.Cursor) -> None:
    cur.executemany(
        """INSERT INTO oems (oem_code, display_name, payload_format, vin_wmi,
                             requires_vin_check_digit)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (oem_code) DO UPDATE SET display_name = EXCLUDED.display_name,
             payload_format = EXCLUDED.payload_format,
             requires_vin_check_digit = EXCLUDED.requires_vin_check_digit""",
        [
            (o.code, o.display_name, o.payload_format, o.vin_wmi, o.requires_vin_check_digit)
            for o in OEMS
        ],
    )
    cur.executemany(
        """INSERT INTO vehicle_models
             (model_code, oem_code, display_name, powertrain, vehicle_class)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (model_code) DO UPDATE SET display_name = EXCLUDED.display_name""",
        [
            (m.model_code, m.oem_code, m.display_name, m.powertrain.value, m.vehicle_class)
            for m in VEHICLE_MODELS
        ],
    )
    cur.executemany(
        """INSERT INTO failure_modes (failure_mode, display_name, component) VALUES (%s, %s, %s)
           ON CONFLICT (failure_mode) DO UPDATE SET display_name = EXCLUDED.display_name,
             component = EXCLUDED.component""",
        [(f.code, f.display_name, f.component) for f in FAILURE_MODES],
    )
    cur.executemany(
        """INSERT INTO failure_mode_powertrains (failure_mode, powertrain) VALUES (%s, %s)
           ON CONFLICT DO NOTHING""",
        [(f.code, p.value) for f in FAILURE_MODES for p in sorted(f.powertrains)],
    )
    cur.executemany(
        """INSERT INTO dtc_codes (dtc_code, description, severity, failure_mode, is_synthetic)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (dtc_code) DO UPDATE SET description = EXCLUDED.description,
             severity = EXCLUDED.severity, failure_mode = EXCLUDED.failure_mode""",
        [(d.code, d.description, d.severity.value, d.failure_mode, d.is_synthetic) for d in DTCS],
    )


def copy_rows(cur: psycopg.Cursor, statement: str, rows: object) -> int:
    count = 0
    with cur.copy(statement) as copy:
        for row in rows:  # type: ignore[attr-defined]
            copy.write_row(row)
            count += 1
    return count


def load_fleet(cur: psycopg.Cursor, roster: Roster) -> dict[str, int]:
    per_tenant = {t.tenant_id: 0 for t in roster.tenants}
    for v in roster.vehicles:
        per_tenant[v.tenant_id] += 1
    counts: dict[str, int] = {}
    counts["tenants"] = copy_rows(
        cur,
        "COPY tenants (tenant_id, slug, display_name) FROM STDIN",
        ((t.tenant_id, t.slug, t.display_name) for t in roster.tenants),
    )
    counts["subscriptions"] = copy_rows(
        cur,
        "COPY subscriptions (tenant_id, plan, vehicle_limit, valid_during) FROM STDIN",
        (
            (t.tenant_id, "enterprise", -(-per_tenant[t.tenant_id] // 1000) * 1000,
             "[2026-01-01,2027-01-01)")
            for t in roster.tenants
        ),
    )  # fmt: skip
    counts["fleets"] = copy_rows(
        cur,
        "COPY fleets (fleet_id, tenant_id, name, base_city) FROM STDIN",
        ((f.fleet_id, f.tenant_id, f.name, f.base_city) for f in roster.fleets),
    )
    counts["workshops"] = copy_rows(
        cur,
        "COPY workshops (workshop_id, tenant_id, name, city, latitude, longitude, daily_capacity)"
        " FROM STDIN",
        (
            (w.workshop_id, w.tenant_id, w.name, w.city, w.latitude, w.longitude, w.daily_capacity)
            for w in roster.workshops
        ),
    )
    counts["vehicles"] = copy_rows(
        cur,
        "COPY vehicles (vehicle_id, tenant_id, fleet_id, vin, model_code, model_year,"
        " firmware_version, home_workshop_id, commissioned_on) FROM STDIN",
        (
            (v.vehicle_id, v.tenant_id, v.fleet_id, v.vin, v.model_code, v.model_year,
             v.firmware_version, v.home_workshop_id, v.commissioned_on)
            for v in roster.vehicles
        ),
    )  # fmt: skip
    counts["drivers"] = copy_rows(
        cur,
        "COPY drivers (driver_id, tenant_id, pseudonym) FROM STDIN",
        ((d.driver_id, d.tenant_id, d.pseudonym) for d in roster.drivers),
    )
    commissioned = {v.vehicle_id: v.commissioned_on for v in roster.vehicles}
    counts["driver_assignments"] = copy_rows(
        cur,
        "COPY driver_assignments (tenant_id, vehicle_id, driver_id, during) FROM STDIN",
        (
            (d.tenant_id, d.assigned_vehicle_id, d.driver_id,
             f"[{commissioned[d.assigned_vehicle_id]},)")
            for d in roster.drivers
            if d.assigned_vehicle_id is not None
        ),
    )  # fmt: skip
    counts["cost_parameters"] = copy_rows(
        cur,
        "COPY cost_parameters (tenant_id, failure_mode, planned_repair_cost, unplanned_repair_cost,"
        " downtime_cost_per_day, extra_downtime_days, currency, source) FROM STDIN",
        (
            (t.tenant_id, code, c.planned_repair_cost, c.unplanned_repair_cost,
             c.downtime_cost_per_day, c.extra_downtime_days, c.currency, c.source)
            for t in roster.tenants
            for code, c in DEFAULT_COSTS.items()
        ),
    )  # fmt: skip
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vehicles", type=int, default=int(os.getenv("VEHICLE_COUNT", "10000")))
    parser.add_argument("--tenants", type=int, default=int(os.getenv("TENANT_COUNT", "20")))
    parser.add_argument("--seed", type=int, default=int(os.getenv("SIM_SEED", "42")))
    parser.add_argument("--reset", action="store_true", help="truncate tenant data first")
    parser.add_argument("--dsn", default=None, help="libpq DSN (default: from POSTGRES_* env)")
    parser.add_argument("--evidence", type=Path, help="write timing/counts JSON here")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    roster = generate_roster(args.vehicles, args.tenants, args.seed)
    generated = time.perf_counter()

    with psycopg.connect(args.dsn or dsn_from_env()) as conn, conn.cursor() as cur:
        upsert_reference_data(cur)
        if args.reset:
            cur.execute(f"TRUNCATE {TENANT_TABLES} CASCADE")
        cur.execute("SELECT count(*) FROM tenants")
        existing = cur.fetchone()[0]  # type: ignore[index]
        if existing:
            print(f"fleet data already present ({existing} tenants); use --reset to reload")
            return 0
        counts = load_fleet(cur, roster)
        cur.execute("ANALYZE")
    finished = time.perf_counter()

    result = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vehicles": args.vehicles,
        "tenants": args.tenants,
        "seed": args.seed,
        "environment": {
            "machine": platform.machine(),
            "system": platform.system(),
            "cpu_count": os.cpu_count(),
        },
        "roster_generation_s": round(generated - started, 3),
        "database_load_s": round(finished - generated, 3),
        "rows": counts,
    }
    print(json.dumps(result, indent=2))
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

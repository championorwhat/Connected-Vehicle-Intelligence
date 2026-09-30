"""Replay the product's real query mix and rank statements by cost (M15).

    uv run python scripts/sql_workload.py --rounds 5 --output evidence/performance/x.json

Rather than guessing which queries are slow, this drives the running API (as a fleet
manager of every tenant: dashboard summary, alert and work-order lists with keyset
paging, vehicle lists and details, the at-risk ranking) and the planner, then reads
`pg_stat_statements` (loaded in docker-compose.yml). The ranking is by total time: what
the database actually spends its time on.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

from prognos_api.main import create_user

PASSWORD = "perf-workload-password"  # noqa: S105 - local measurement accounts only


def dsn() -> str:
    e = os.environ
    return make_conninfo(
        host=e.get("POSTGRES_SEED_HOST", "localhost"),
        port=e.get("POSTGRES_HOST_PORT", "5432"),
        dbname=e.get("POSTGRES_DB", "prognos"),
        user=e.get("POSTGRES_USER", "prognos"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )


def dashboard_session(api: httpx.Client, email: str) -> int:
    """What a fleet manager's dashboard does in one visit; returns requests made."""
    token = api.post("/v1/auth/token", data={"username": email, "password": PASSWORD})
    token.raise_for_status()
    api.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
    calls = 0

    def get(path: str, **params: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        r = api.get(path, params=params)
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        return body

    def paged(path: str, pages: int, **params: Any) -> list[dict[str, Any]]:
        items, cursor = [], None
        for _ in range(pages):
            body = get(path, **params, **({"cursor": cursor} if cursor else {}))
            items += body["items"]
            if not (cursor := body.get("next_cursor")):
                break
        return items

    get("/v1/fleet/summary")
    get("/v1/vehicles/at-risk")
    paged("/v1/alerts", 5)
    paged("/v1/alerts", 3, status="open")
    paged("/v1/alerts", 2, status="open", severity="critical")
    paged("/v1/work-orders", 3)
    paged("/v1/work-orders", 2, status="proposed")
    vehicles = paged("/v1/vehicles", 4)
    for v in vehicles[:: max(len(vehicles) // 5, 1)][:5]:
        get(f"/v1/vehicles/{v['vehicle_id']}")
        paged("/v1/alerts", 1, vehicle_id=v["vehicle_id"])
    return calls


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--planner-runs", type=int, default=3)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--top", type=int, default=12)
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    with psycopg.connect(dsn()) as conn:
        slugs = [r[0] for r in conn.execute("SELECT slug FROM tenants ORDER BY slug")]
    emails = [f"perf-{slug}@prognos.local" for slug in slugs]
    for slug, email in zip(slugs, emails, strict=True):
        asyncio.run(create_user(dsn(), email, slug, ["fleet_manager"], "perf", PASSWORD))

    with psycopg.connect(dsn(), autocommit=True) as conn:
        conn.execute("SELECT pg_stat_statements_reset()")
    started = time.perf_counter()
    requests = 0
    with httpx.Client(base_url=args.api, timeout=60) as api:
        for _ in range(args.rounds):
            for email in emails:
                requests += dashboard_session(api, email)
    api_seconds = time.perf_counter() - started
    for _ in range(args.planner_runs):
        # --no-deps: running the planner must not re-run migrations (it depends on them)
        planner = ["docker", "compose", "--profile", "pipeline", "run", "--rm", "--no-deps", "-T",
                   "planner"]  # fmt: skip
        subprocess.run([*planner, "--once"], check=True, capture_output=True)  # noqa: S603 - fixed argv

    with psycopg.connect(dsn()) as conn:
        rows = conn.execute(
            """
            SELECT queryid::text, calls, round(total_exec_time::numeric, 1) AS total_ms,
                   round(mean_exec_time::numeric, 2) AS mean_ms,
                   round(max_exec_time::numeric, 2) AS max_ms, rows,
                   shared_blks_hit + shared_blks_read AS blocks,
                   regexp_replace(query, '\\s+', ' ', 'g') AS query
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query !~* '^(SET|RESET|BEGIN|COMMIT|ROLLBACK|SELECT set_config|SELECT \\$1)'
              AND query !~* 'pg_stat_statements|INSERT INTO users|INSERT INTO user_roles'
            ORDER BY total_exec_time DESC LIMIT %s
            """,
            (args.top,),
        ).fetchall()
        cols = ["queryid", "calls", "total_ms", "mean_ms", "max_ms", "rows", "blocks", "query"]
        top: list[dict[str, Any]] = [
            dict(zip(cols, [float(v) if hasattr(v, "is_finite") else v for v in r], strict=True))
            for r in rows
        ]
        sizes: dict[str, int] = dict(conn.execute(
            "SELECT relname, n_live_tup FROM pg_stat_user_tables"
            " WHERE relname IN ('vehicles', 'alerts', 'work_orders')").fetchall())  # fmt: skip

    result = {
        "measured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "workload": {"tenants": len(slugs), "rounds": args.rounds, "api_requests": requests,
                     "api_seconds": round(api_seconds, 1), "planner_runs": args.planner_runs},
        "table_rows": sizes,
        "top_by_total_time": top,
    }  # fmt: skip
    text = json.dumps(result, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    for i, q in enumerate(top, 1):
        print(f"{i:2d}. {q['total_ms']:>10} ms total  {q['mean_ms']:>8} ms mean  "
              f"{q['calls']:>5} calls  {q['query'][:110]}")  # fmt: skip
    print(f"{requests} API requests in {api_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

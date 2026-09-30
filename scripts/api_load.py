"""Concurrent dashboard users against the running API (M14).

    uv run python scripts/api_load.py --users 40 --duration 180 --output evidence/load-tests/x.json

Each virtual user is a fleet manager (spread over every tenant). It signs in once, then
repeats what the dashboard does on a visit, with a think time between clicks: summary,
at-risk list, alerts, work orders, vehicle list, and one vehicle's detail. Latency is
measured client-side per route (request sent to last byte received), so it includes the
API, PostgreSQL, Redis and the network stack.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import random
import statistics
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

from prognos_api.main import create_user

PASSWORD = "api-load-password"  # noqa: S105 - local measurement accounts only
CLICKS = [
    ("summary", "/v1/fleet/summary", {}),
    ("at_risk", "/v1/vehicles/at-risk", {}),
    ("alerts", "/v1/alerts", {}),
    ("alerts_open", "/v1/alerts", {"status": "open"}),
    ("work_orders", "/v1/work-orders", {}),
    ("vehicles", "/v1/vehicles", {}),
]


def dsn() -> str:
    e = os.environ
    return make_conninfo(
        host=e.get("POSTGRES_SEED_HOST", "localhost"),
        port=e.get("POSTGRES_HOST_PORT", "5432"),
        dbname=e.get("POSTGRES_DB", "prognos"),
        user=e.get("POSTGRES_USER", "prognos"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(int(q * len(ordered)), len(ordered) - 1)] * 1000, 1)


async def user(api: httpx.AsyncClient, email: str, until: float, think: float,
               results: dict[str, list[float]], errors: dict[str, int]) -> None:  # fmt: skip
    r = await api.post("/v1/auth/token", data={"username": email, "password": PASSWORD})
    r.raise_for_status()
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    vehicle_ids: list[str] = []
    while time.monotonic() < until:
        for name, path, params in [*CLICKS, ("vehicle", None, {})]:
            if path is None:
                if not vehicle_ids:
                    continue
                path = f"/v1/vehicles/{random.choice(vehicle_ids)}"  # noqa: S311
            started = time.perf_counter()
            try:
                resp = await api.get(path, params=params, headers=headers)
                elapsed = time.perf_counter() - started
                if resp.status_code == 200:
                    results.setdefault(name, []).append(elapsed)
                    if name == "vehicles":
                        vehicle_ids = [v["vehicle_id"] for v in resp.json()["items"]]
                else:
                    errors[f"{name}:{resp.status_code}"] = (
                        errors.get(f"{name}:{resp.status_code}", 0) + 1
                    )
            except httpx.HTTPError as exc:
                errors[f"{name}:{type(exc).__name__}"] = (
                    errors.get(f"{name}:{type(exc).__name__}", 0) + 1
                )
            await asyncio.sleep(think * random.uniform(0.5, 1.5))  # noqa: S311
            if time.monotonic() >= until:
                return


async def run(args: argparse.Namespace) -> dict[str, Any]:
    with psycopg.connect(dsn()) as conn:
        slugs = [r[0] for r in conn.execute("SELECT slug FROM tenants ORDER BY slug")]
    emails = []
    for i in range(args.users):
        slug = slugs[i % len(slugs)]
        email = f"load-{i}@prognos.local"
        await create_user(dsn(), email, slug, ["fleet_manager"], f"load {i}", PASSWORD)
        emails.append(email)
    results: dict[str, list[float]] = {}
    errors: dict[str, int] = {}
    limits = httpx.Limits(max_connections=args.users, max_keepalive_connections=args.users)
    async with httpx.AsyncClient(base_url=args.api, timeout=30, limits=limits) as api:
        started = time.monotonic()
        until = started + args.duration
        await asyncio.gather(*(user(api, e, until, args.think, results, errors) for e in emails))
        elapsed = time.monotonic() - started
    total = sum(len(v) for v in results.values())
    return {
        "measured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "users": args.users, "duration_s": round(elapsed, 1), "think_s": args.think,
        "requests_ok": total, "requests_per_s": round(total / elapsed, 1),
        "errors": errors,
        "error_rate": round(sum(errors.values()) / max(total + sum(errors.values()), 1), 4),
        "latency_ms": {name: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95),
                              "p99": pct(v, 0.99), "mean": round(statistics.fmean(v) * 1000, 1)}
                       for name, v in sorted(results.items())},
        "all_routes_p95_ms": pct([x for v in results.values() for x in v], 0.95),
    }  # fmt: skip


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--users", type=int, default=40)
    p.add_argument("--duration", type=float, default=180)
    p.add_argument("--think", type=float, default=1.0, help="seconds between clicks (mean)")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    result = asyncio.run(run(args))
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

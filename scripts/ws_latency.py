"""Dashboard freshness: vehicle event -> alert on the dashboard's WebSocket (M17).

    uv run python scripts/ws_latency.py --duration 300 --output evidence/load-tests/x.json

The brief asks for "< 2 s to the dashboard". This measures exactly the path the browser
uses: it signs in one fleet manager per tenant, opens the live alert feed
(`/v1/ws/alerts`) like the dashboard does, and for every alert that arrives records

  - end to end: receipt time - the event's own timestamp (`event_ts`, set by the vehicle),
  - delivery:   receipt time - `detected_at` (detector -> Kafka -> sink -> Redis -> API -> socket).

All processes run on one host, so the clocks are the same clock. The simulator adds its
own network delay (mean 0.3 s) to `event_ts`, as a real vehicle's uplink would.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from websockets.asyncio.client import connect

from prognos_api.main import create_user

PASSWORD = "ws-latency-password"  # noqa: S105 - local measurement accounts only


def dsn() -> str:
    e = os.environ
    return make_conninfo(
        host=e.get("POSTGRES_SEED_HOST", "localhost"),
        port=e.get("POSTGRES_HOST_PORT", "5432"),
        dbname=e.get("POSTGRES_DB", "prognos"),
        user=e.get("POSTGRES_USER", "prognos"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )


def epoch(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(int(q * len(ordered)), len(ordered) - 1)], 3)


def summary(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "p50_s": pct(values, 0.5), "p95_s": pct(values, 0.95),
            "p99_s": pct(values, 0.99), "max_s": round(max(values), 3) if values else None,
            "share_under_2s": round(sum(v < 2 for v in values) / len(values), 4)
            if values else None}  # fmt: skip


async def listen(api_url: str, token: str, until: float, rows: list[dict[str, Any]]) -> None:
    ws_url = api_url.replace("http", "ws", 1) + "/v1/ws/alerts"
    async with connect(ws_url) as ws:
        await ws.send(json.dumps({"token": token}))
        json.loads(await ws.recv())  # {"type": "subscribed", ...}
        while (left := until - time.time()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=left)
            except TimeoutError:
                break
            received = time.time()
            a = json.loads(raw)
            if a.get("status") != "open":
                continue
            rows.append({"severity": a["severity"], "rule": a["rule_code"],
                         "end_to_end_s": received - epoch(a["event_ts"]),
                         "delivery_s": received - epoch(a["detected_at"])})  # fmt: skip


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--label", default="")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    with psycopg.connect(dsn()) as conn:
        tenants = [r[0] for r in conn.execute("SELECT slug FROM tenants ORDER BY 1")]
    tokens = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as api:
        for i, tenant in enumerate(tenants):
            email = f"ws-latency-{i}@load.prognos.local"
            await create_user(dsn(), email, tenant, ["fleet_manager"], f"ws {i}", PASSWORD)
            r = await api.post("/v1/auth/token", data={"username": email, "password": PASSWORD})
            r.raise_for_status()
            tokens.append(r.json()["access_token"])

    rows: list[dict[str, Any]] = []
    started = time.time()
    until = started + args.duration
    await asyncio.gather(*(listen(args.api, t, until, rows) for t in tokens))
    critical = [r for r in rows if r["severity"] == "critical"]
    severities = sorted({r["severity"] for r in rows})
    result = {
        "what": "vehicle event -> opened alert received on the dashboard WebSocket",
        "label": args.label,
        "hardware": "4-vCPU x86_64 Linux container, all services on shared CPUs (not the Mac)",
        "started_utc": dt.datetime.fromtimestamp(started, dt.UTC).isoformat(),
        "duration_s": args.duration,
        "tenants_subscribed": len(tokens),
        "target": "< 2 s to the dashboard (brief)",
        "end_to_end_all": summary([r["end_to_end_s"] for r in rows]),
        "end_to_end_critical": summary([r["end_to_end_s"] for r in critical]),
        "delivery_detected_to_socket": summary([r["delivery_s"] for r in rows]),
        "by_severity": {s: sum(r["severity"] == s for r in rows) for s in severities},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    keys = ("end_to_end_all", "end_to_end_critical", "delivery_detected_to_socket")
    print(json.dumps({k: result[k] for k in keys}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

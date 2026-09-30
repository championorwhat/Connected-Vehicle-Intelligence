"""When does each scripted demo failure show up? (M18 demo rehearsal)

    make pipeline-demo users            # start the demo stack
    uv run python scripts/demo_timeline.py --duration 1200 --output evidence/demo/x.json

The `demo` scenario forces one vehicle per failure mode to fail 10-18 minutes after the
simulator starts. This script reads those vehicles from the simulator's log, then polls
PostgreSQL and records, relative to the simulator's start, when each vehicle's alerts
are raised and when the planner proposes its work order. The presenter uses the result
to know when to start recording, and which tenant to sign in to.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

DEMO_LINE = re.compile(r"demo: (\w{17}) will fail with (\w+)")

ALERTS_SQL = """
SELECT v.vin, t.slug, a.rule_code, a.severity, a.event_ts, a.detected_at
FROM alerts a JOIN vehicles v USING (vehicle_id) JOIN tenants t ON t.tenant_id = a.tenant_id
WHERE v.vin = ANY(%s)
"""
ORDERS_SQL = """
SELECT v.vin, w.failure_mode, w.status, w.created_at, w.scheduled_for
FROM work_orders w JOIN vehicles v USING (vehicle_id)
WHERE v.vin = ANY(%s)
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


def simulator() -> tuple[float, dict[str, str]]:
    """The simulator container's start time and {vin: failure mode} of the demo vehicles."""
    started = subprocess.run(
        ["docker", "compose", "--profile", "pipeline", "ps", "-q", "simulator"],  # noqa: S607
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    if not started:
        raise SystemExit("the simulator is not running: start it with `make pipeline-demo`")
    iso = subprocess.run(  # noqa: S603
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", started],  # noqa: S607
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    start = dt.datetime.fromisoformat(iso[:26].rstrip("Z") + "+00:00").timestamp()
    logs = subprocess.run(
        ["docker", "compose", "--profile", "pipeline", "logs", "--no-color", "simulator"],  # noqa: S607
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    vins = dict(DEMO_LINE.findall(logs))
    if not vins:
        raise SystemExit("no demo vehicles in the simulator log: was SIM_SCENARIO=demo set?")
    return start, vins


def mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def line(row: dict[str, Any]) -> str:
    what = (f"{row['severity']:8} {row['rule']}" if "rule" in row
            else f"work order {row['status']} ({row['failure_mode']})")  # fmt: skip
    return f"{row['at']}  {row['vin']}  {what}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=float, default=1200, help="seconds after simulator start")
    p.add_argument("--interval", type=float, default=10)
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    start, vins = simulator()
    print(f"simulator started {mmss(time.time() - start)} ago; demo vehicles: {vins}", flush=True)
    alerts: dict[tuple[str, str], dict[str, Any]] = {}
    orders: dict[tuple[str, str], dict[str, Any]] = {}
    printed: set[str] = set()
    with psycopg.connect(dsn(), autocommit=True) as conn:
        while True:
            for vin, slug, rule, severity, event_ts, detected in conn.execute(
                ALERTS_SQL, [list(vins)]
            ):
                alerts.setdefault((vin, rule), {
                    "vin": vin, "tenant": slug, "rule": rule, "severity": severity,
                    "at": mmss(detected.timestamp() - start),
                    "seconds": round(detected.timestamp() - start, 1),
                    "latency_s": round((detected - event_ts).total_seconds(), 2),
                })  # fmt: skip
            for vin, mode, status, created, scheduled in conn.execute(ORDERS_SQL, [list(vins)]):
                orders.setdefault((vin, mode), {
                    "vin": vin, "failure_mode": mode, "status": status,
                    "at": mmss(created.timestamp() - start),
                    "seconds": round(created.timestamp() - start, 1),
                    "scheduled_for": str(scheduled) if scheduled else None,
                })  # fmt: skip
            rows = sorted([*alerts.values(), *orders.values()], key=lambda r: r["seconds"])
            for row in rows:
                if line(row) not in printed:  # live cue sheet while recording
                    printed.add(line(row))
                    print(line(row), flush=True)
            if time.time() - start >= args.duration:
                break
            time.sleep(args.interval)

    result = {
        "what": "demo rehearsal: when each scripted failure becomes visible",
        "simulator_started_utc": dt.datetime.fromtimestamp(start, dt.UTC).isoformat(),
        "demo_vehicles": vins,
        "tenants": sorted({a["tenant"] for a in alerts.values()}),
        "alerts": sorted(alerts.values(), key=lambda r: r["seconds"]),
        "work_orders": sorted(orders.values(), key=lambda r: r["seconds"]),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

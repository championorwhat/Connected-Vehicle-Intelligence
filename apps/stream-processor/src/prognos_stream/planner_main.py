"""prognos-planner: turn open alerts into proposed, capacity-aware work orders.

    prognos-planner                 # every PLANNER_INTERVAL_SECONDS (default 60)
    prognos-planner --once --output plan.json

Each cycle, in one PostgreSQL transaction:
  1. reads open/acknowledged alerts that have a failure mode and no active work
     order yet, the tenants' workshops and capacity already booked, and costs;
  2. locates each vehicle (live position from Redis if available, else its home
     workshop) and runs planner.build_candidates + planner.schedule;
  3. inserts one 'proposed' work order per scheduled (vehicle, failure mode) and
     an audit_log row for each. The partial unique index on active work orders
     makes this idempotent: a second planner replica, or a re-run, inserts nothing.

A human (or the M9 API) moves a proposal to 'scheduled'; the planner never does.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import orjson
import psycopg
import redis
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from psycopg.types.json import Jsonb

from prognos_common.logs import configure
from prognos_stream.planner import (
    Calibration,
    Candidate,
    Costs,
    ModelRisk,
    OpenAlert,
    Workshop,
    build_candidates,
    schedule,
)

log = logging.getLogger("prognos_stream")

ACTIVE = ("proposed", "scheduled", "in_progress")
PROPOSED = Counter("prognos_planner_work_orders_proposed", "Work orders proposed")
LATE = Counter("prognos_planner_late_proposals", "Proposals booked after the predicted failure")
UNSCHEDULED = Gauge("prognos_planner_unscheduled", "At-risk vehicles with no free slot")
QUEUE = Gauge("prognos_planner_queue", "At-risk vehicles considered in the last cycle")
LAST_SUCCESS = Gauge("prognos_planner_last_success_timestamp_seconds",
                     "Unix time of the last completed planning cycle")  # fmt: skip
CYCLE = Histogram("prognos_planner_cycle_seconds", "Duration of one planning cycle",
                  buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))  # fmt: skip

_ALERTS = """
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
_WORKSHOPS = """
    SELECT workshop_id::text, tenant_id::text, latitude::float8, longitude::float8, daily_capacity
    FROM workshops
"""
_BOOKED = """
    SELECT workshop_id::text, scheduled_for - current_date, count(*)
    FROM work_orders
    WHERE status IN ('proposed', 'scheduled', 'in_progress') AND scheduled_for >= current_date
    GROUP BY 1, 2
"""
_COSTS = """
    SELECT tenant_id::text, failure_mode, planned_repair_cost::float8,
           unplanned_repair_cost::float8, downtime_cost_per_day::float8,
           extra_downtime_days::float8, currency, source
    FROM cost_parameters
"""
_INSERT = """
    INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, source_alert_id, failure_mode,
                             status, failure_probability, expected_cost_avoided, model_version,
                             scheduled_for)
    VALUES (%s, %s, %s, %s, %s, 'proposed', %s, %s, %s, %s)
    ON CONFLICT (vehicle_id, failure_mode) WHERE status IN ('proposed', 'scheduled', 'in_progress')
    DO NOTHING
    RETURNING work_order_id::text
"""
_AUDIT = """
    INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, resource_type, resource_id,
                           outcome, details)
    VALUES (%s, 'system', 'prognos-planner', 'work_order.propose', 'work_order', %s, 'success', %s)
"""


def locate(client: redis.Redis | None, vehicle_ids: list[str]) -> dict[str, tuple[float, float]]:
    """Latest position per vehicle from the live state in Redis; missing -> not returned."""
    if client is None or not vehicle_ids:
        return {}
    found: dict[str, tuple[float, float]] = {}
    try:
        for start in range(0, len(vehicle_ids), 1000):
            chunk = vehicle_ids[start : start + 1000]
            for vid, raw in zip(chunk, client.mget([f"veh:{v}" for v in chunk]), strict=True):
                if raw is None:
                    continue
                s = orjson.loads(raw)
                if s.get("latitude") is not None and s.get("longitude") is not None:
                    found[vid] = (float(s["latitude"]), float(s["longitude"]))
    except (redis.ConnectionError, redis.TimeoutError) as exc:
        # Redis is AP and rebuildable (ADR-004): plan with home locations rather than stall.
        log.warning("redis unavailable, using home workshop locations: %s", exc)
    return found


def model_scores(client: redis.Redis | None, vehicle_ids: list[str]) -> dict[str, ModelRisk]:
    """Fresh live-model scores (the scorer's keys expire, so present means recent)."""
    if client is None or not vehicle_ids:
        return {}
    found: dict[str, ModelRisk] = {}
    try:
        for start in range(0, len(vehicle_ids), 1000):
            chunk = vehicle_ids[start : start + 1000]
            for vid, raw in zip(chunk, client.mget([f"veh:{v}:risk" for v in chunk]), strict=True):
                if raw is not None:
                    s = orjson.loads(raw)
                    found[vid] = ModelRisk(float(s["probability"]), str(s["model_version"]))
    except (redis.ConnectionError, redis.TimeoutError) as exc:
        log.warning("redis unavailable, planning with calibrated rules: %s", exc)
    return found


def plan_once(
    conn: psycopg.Connection[Any],
    calibration: Calibration,
    live: redis.Redis | None = None,
    *,
    horizon_days: int = 7,
    nearest_k: int = 3,
    use_model: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    with conn.transaction(), conn.cursor() as cur:
        # One planner at a time per database: a second replica waits, then sees the
        # first one's proposals and adds none (the unique index is the safety net).
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('prognos-planner'))")
        cur.execute("SELECT current_date")
        today: date = cur.fetchone()[0]  # type: ignore[index]
        rows = cur.execute(_ALERTS).fetchall()
        alerts = [OpenAlert(r[0], r[1], r[2], r[3], r[4], r[5], float(r[6]), r[7]) for r in rows]
        home = {r[2]: (r[8], r[9]) for r in rows if r[8] is not None}
        workshops = [Workshop(*r) for r in cur.execute(_WORKSHOPS).fetchall()]
        booked = {(w, int(d)): int(n) for w, d, n in cur.execute(_BOOKED).fetchall()}
        costs = {(r[0], r[1]): Costs(*r[2:]) for r in cur.execute(_COSTS).fetchall()}

        # Shadow deployment (ADR-008): model scores are used only when enabled.
        risk = model_scores(live, sorted({a.vehicle_id for a in alerts})) if use_model else {}
        candidates = build_candidates(alerts, calibration, costs, risk)
        positions = locate(live, sorted({c.vehicle_id for c in candidates}))
        for c in candidates:
            c.location = positions.get(c.vehicle_id) or home.get(c.vehicle_id)
        ordered = schedule(
            candidates, workshops, booked, horizon_days=horizon_days, nearest_k=nearest_k
        )

        created: list[tuple[str, Candidate]] = []
        chosen = [c for c in ordered if c.workshop_id is not None and c.day is not None]
        if chosen:
            # One pipelined batch instead of a round trip per row.
            cur.executemany(
                _INSERT,
                [
                    (c.tenant_id, c.vehicle_id, c.workshop_id, c.source_alert_id,
                     c.failure_mode, round(c.p_failure, 4), c.expected_cost_avoided,
                     c.model_version, today + timedelta(days=c.day or 0))
                    for c in chosen
                ],
                returning=True,
            )  # fmt: skip
            for c in chosen:
                row = cur.fetchone()
                if row is not None:  # None: another planner proposed it first
                    created.append((row[0], c))
                if not cur.nextset():
                    break
            if created:
                cur.executemany(
                    _AUDIT, [(c.tenant_id, wid, Jsonb(explain(c))) for wid, c in created]
                )

    late = sum(1 for _, c in created if c.late)
    unscheduled = [c for c in ordered if c.workshop_id is None]
    PROPOSED.inc(len(created))
    LATE.inc(late)
    UNSCHEDULED.set(len(unscheduled))
    QUEUE.set(len(ordered))
    CYCLE.observe(time.perf_counter() - started)
    LAST_SUCCESS.set_to_current_time()
    return {
        "planned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "calibration_version": calibration.version,
        "open_alerts_without_work_order": len(alerts),
        "at_risk_vehicle_modes": len(ordered),
        "work_orders_proposed": len(created),
        "proposed_after_predicted_failure": late,
        "unscheduled_no_capacity": len(unscheduled),
        "value_basis": sorted({c.value_basis for c in ordered}),
        "risk_sources": sorted({c.model_version for c in ordered}),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "top": [explain(c) | {"work_order_id": wid} for wid, c in created[:10]],
    }


def explain(c: Candidate) -> dict[str, Any]:
    return {
        "vehicle_id": c.vehicle_id,
        "failure_mode": c.failure_mode,
        "rule_code": c.rule_code,
        "p_failure_7d": round(c.p_failure, 3),
        "hours_to_predicted_failure": (
            None if c.hours_remaining is None else round(c.hours_remaining, 1)
        ),
        "value": round(c.value, 4),
        "value_basis": c.value_basis,
        "expected_cost_avoided": c.expected_cost_avoided,
        "currency": c.currency,
        "workshop_id": c.workshop_id,
        "day_offset": c.day,
        "distance_km": c.distance_km,
        "after_predicted_failure": c.late,
        "reasons": c.reasons,
    }


def dsn_from_env() -> str:
    env = os.environ
    return (
        f"host={env.get('POSTGRES_HOST', 'localhost')} port={env.get('POSTGRES_PORT', '5432')} "
        f"dbname={env.get('POSTGRES_DB', 'prognos')} user={env.get('POSTGRES_USER', 'prognos')} "
        f"password={env.get('POSTGRES_PASSWORD', '')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="plan one cycle and exit")
    parser.add_argument("--output", type=Path, help="write the cycle summary as JSON")
    args = parser.parse_args(argv)
    configure("planner")
    env = os.environ
    interval = float(env.get("PLANNER_INTERVAL_SECONDS", "60"))
    horizon = int(env.get("PLANNER_HORIZON_DAYS", "7"))
    use_model = env.get("PLANNER_RISK_SOURCE", "rules") == "model"
    calibration = Calibration.load()
    live = None
    if env.get("REDIS_HOST"):
        live = redis.Redis(
            host=env["REDIS_HOST"], port=int(env.get("REDIS_PORT", "6379")),
            password=env.get("REDIS_PASSWORD") or None, socket_timeout=5,
        )  # fmt: skip
    if not args.once and (port := int(env.get("METRICS_PORT", "9104"))):
        start_http_server(port)

    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stop:
        try:
            with psycopg.connect(dsn_from_env(), connect_timeout=5) as conn:
                summary = plan_once(
                    conn, calibration, live, horizon_days=horizon, use_model=use_model
                )
            log.info("plan: %s", {k: v for k, v in summary.items() if k != "top"})
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(summary, indent=2) + "\n")
        except psycopg.OperationalError as exc:
            if args.once:
                raise
            log.warning("postgres unavailable, retrying next cycle: %s", exc)
        if args.once:
            break
        deadline = time.monotonic() + interval
        while not stop and time.monotonic() < deadline:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())

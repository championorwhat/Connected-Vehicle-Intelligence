"""Right-to-erasure worker: carries out requests a DPO filed through the API.

    received ──worker──► in_progress ──► completed
        ▲                    │
        └──── retry ─────────┘ (store unavailable; attempts recorded)
    received ──► rejected (subject no longer exists)

What is erased (docs/security/privacy.md):
- driver:  the link between the pseudonym and vehicles (driver_assignments rows), so
           no telemetry or location can be tied to the person; drivers.erased_at is set.
- vehicle: location history. ClickHouse events.latitude/longitude become NaN, the Redis
           live state and last-known position are deleted, and driver links are removed.
           Maintenance data (alerts, work orders, sensor readings) is kept: it is not
           location data, and fleets need it for safety and warranty records.

Raw Kafka topics cannot be edited in place; they expire by retention (telemetry 1 day
by default, DLQ 7 days). The completed request records that residual window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

log = logging.getLogger("prognos_api.erasure")

RESIDUAL = ("Raw Kafka topics keep original payloads until retention expires "
            "(telemetry 1 day by default, DLQ 7 days); ClickHouse dlq_events keeps "
            "quarantined payloads for 7 days.")  # fmt: skip
MAX_ATTEMPTS = 5


class ClickHouse(Protocol):
    def command(self, cmd: str, *, parameters: Any = None, settings: Any = None) -> Any: ...


class Redis(Protocol):
    def delete(self, *names: str) -> int: ...
    def zrem(self, name: str, *values: str) -> int: ...


@dataclass
class Outcome:
    request_id: str
    status: str
    details: dict[str, Any]


_CLAIM = """
    UPDATE erasure_requests SET status = 'in_progress'
    WHERE request_id = (
        SELECT request_id FROM erasure_requests WHERE status = 'received'
        ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED
    )
    RETURNING request_id::text, tenant_id::text, subject_type, subject_id::text, details
"""


def _audit(conn: psycopg.Connection[Any], req: dict[str, Any], outcome: str,
           details: dict[str, Any]) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, resource_type,"
        " resource_id, outcome, details) VALUES (%s, 'system', 'erasure-worker',"
        " 'privacy.erasure_execute', 'erasure_request', %s, %s, %s)",
        (req["tenant_id"], req["request_id"], outcome, Jsonb(details)),
    )


def _erase_driver(conn: psycopg.Connection[Any], req: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute("SELECT 1 FROM drivers WHERE driver_id = %s AND tenant_id = %s",
                       (req["subject_id"], req["tenant_id"])).fetchone()  # fmt: skip
    if row is None:
        return None
    links = conn.execute("DELETE FROM driver_assignments WHERE driver_id = %s AND tenant_id = %s",
                         (req["subject_id"], req["tenant_id"])).rowcount  # fmt: skip
    conn.execute("UPDATE drivers SET erased_at = coalesce(erased_at, now()) WHERE driver_id = %s",
                 (req["subject_id"],))  # fmt: skip
    return {"driver_assignments_deleted": links}


def _erase_vehicle(conn: psycopg.Connection[Any], req: dict[str, Any], ch: ClickHouse,
                   redis: Redis) -> dict[str, Any] | None:  # fmt: skip
    row = conn.execute("SELECT 1 FROM vehicles WHERE vehicle_id = %s AND tenant_id = %s",
                       (req["subject_id"], req["tenant_id"])).fetchone()  # fmt: skip
    if row is None:
        return None
    # External stores first: if one fails, the PostgreSQL transaction rolls back and the
    # request is retried; every step is idempotent.
    ch.command(
        "ALTER TABLE events UPDATE latitude = nan, longitude = nan"
        " WHERE tenant_id = %(t)s AND vehicle_id = %(v)s",
        parameters={"t": req["tenant_id"], "v": req["subject_id"]},
        settings={"mutations_sync": 1},  # wait until applied, so "completed" is true
    )
    redis_keys = redis.delete(f"veh:{req['subject_id']}")
    redis_geo = redis.zrem(f"tenant:{req['tenant_id']}:geo", req["subject_id"])
    links = conn.execute("DELETE FROM driver_assignments WHERE vehicle_id = %s AND tenant_id = %s",
                         (req["subject_id"], req["tenant_id"])).rowcount  # fmt: skip
    return {"clickhouse_location_erased": True, "redis_live_state_deleted": redis_keys,
            "redis_position_deleted": redis_geo, "driver_assignments_deleted": links}  # fmt: skip


def process_one(dsn: str, ch: ClickHouse, redis: Redis) -> Outcome | None:
    """Claim and carry out the oldest received request; None when there is nothing to do."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            req = conn.execute(_CLAIM).fetchone()
        if req is None:
            return None
        try:
            with conn.transaction():
                if req["subject_type"] == "driver":
                    done = _erase_driver(conn, req)
                else:
                    done = _erase_vehicle(conn, req, ch, redis)
                status = "completed" if done is not None else "rejected"
                details = (done | {"residual": RESIDUAL}) if done is not None else {
                    "reason": f"{req['subject_type']} not found in this tenant"}  # fmt: skip
                # clock_timestamp(), not now(): now() is the transaction start, before the
                # ClickHouse mutation (seconds on a large table; found in the M12 live run).
                conn.execute(
                    "UPDATE erasure_requests SET status = %s, details = details || %s,"
                    " completed_at = CASE WHEN %s = 'completed' THEN clock_timestamp() END"
                    " WHERE request_id = %s",
                    (status, Jsonb(details), status, req["request_id"]),
                )
                _audit(conn, req, "success" if done is not None else "error", details)
            log.info("erasure %s %s", req["request_id"], status)
            return Outcome(req["request_id"], status, details)
        except Exception as exc:  # a store is down: give the request back, count the attempt
            attempts = int(req["details"].get("attempts", 0)) + 1
            status = "received" if attempts < MAX_ATTEMPTS else "rejected"
            details = {"attempts": attempts, "last_error": type(exc).__name__}
            with conn.transaction():
                conn.execute(
                    "UPDATE erasure_requests SET status = %s, details = details || %s"
                    " WHERE request_id = %s", (status, Jsonb(details), req["request_id"]),
                )  # fmt: skip
                _audit(conn, req, "error", details)
            log.warning("erasure %s failed (attempt %d): %s", req["request_id"], attempts, exc)
            return Outcome(req["request_id"], status, details)


def process_pending(dsn: str, ch: ClickHouse, redis: Redis, limit: int = 100) -> list[Outcome]:
    outcomes: list[Outcome] = []
    while len(outcomes) < limit and (outcome := process_one(dsn, ch, redis)) is not None:
        outcomes.append(outcome)
        if outcome.status == "received":  # a store is down; stop and retry next cycle
            break
    return outcomes

"""Idempotent stores for the sink service: alerts -> PostgreSQL, live state -> Redis.

PostgreSQL (CP, system of record for alerts):
  open    -> INSERT ... ON CONFLICT (tenant_id, fingerprint) DO NOTHING
  cleared -> UPDATE ... SET status='resolved', resolved_at=... (only if not already)
  Replaying any batch leaves the table unchanged, so at-least-once delivery is safe.
  A batch is one transaction; if a row violates a constraint (e.g. an unknown
  vehicle) the batch is retried row by row inside savepoints and only the bad row
  is rejected, so one poison message cannot block the partition.

Redis (AP, rebuildable from Kafka):
  veh:{vehicle_id}              latest vehicle.state JSON, TTL 1 h
  tenant:{tenant_id}:health     sorted set: vehicle_id scored by health (lowest = most at risk)
  tenant:{tenant_id}:geo        geo set of last positions (map / radius queries)
  PUBLISH alerts:{tenant_id}    live alert transitions for WebSocket fan-out (M9)

Transient failures (connection refused, timeouts) are retried with exponential
backoff and full jitter up to a time budget; after that the error propagates, the
offsets are not committed and the process restarts (Kafka re-delivers).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import orjson
import psycopg
import redis
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

STATE_TTL_S = 3600


def retry[T](
    operation: Callable[[], T],
    *,
    retry_on: tuple[type[BaseException], ...],
    budget_s: float = 60.0,
    base_s: float = 0.2,
    cap_s: float = 5.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Exponential backoff with full jitter (AWS architecture blog), bounded by a time budget."""
    deadline = time.monotonic() + budget_s
    attempt = 0
    while True:
        try:
            return operation()
        except retry_on as exc:
            attempt += 1
            if time.monotonic() >= deadline:
                raise
            if on_retry:
                on_retry(attempt, exc)
            # exponent capped: 2**attempt would overflow float conversion after ~1000 attempts
            sleep(random.uniform(0, min(cap_s, base_s * 2 ** min(attempt, 16))))  # noqa: S311


class AlertStore:
    _OPEN = """
        INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity,
                            failure_mode, status, event_ts, detected_at, details)
        VALUES (%(tenant_id)s, %(vehicle_id)s, %(fingerprint)s, %(rule_code)s, %(severity)s,
                %(failure_mode)s, 'open', %(event_ts)s, %(detected_at)s, %(details)s)
        ON CONFLICT (tenant_id, fingerprint) DO NOTHING
    """
    _CLEAR = """
        UPDATE alerts
        SET status = CASE WHEN status IN ('open', 'acknowledged') THEN 'resolved' ELSE status END,
            resolved_at = COALESCE(resolved_at, GREATEST(%(detected_at)s::timestamptz, detected_at))
        WHERE tenant_id = %(tenant_id)s AND fingerprint = %(fingerprint)s
    """

    def __init__(self, dsn: str, *, retry_budget_s: float = 60.0) -> None:
        self.dsn = dsn
        self.retry_budget_s = retry_budget_s
        self.conn: psycopg.Connection[Any] | None = None
        self.stats = {"opened": 0, "cleared": 0, "rejected": 0, "retries": 0}

    @staticmethod
    def params(alert: dict[str, Any]) -> dict[str, Any]:
        return {
            "tenant_id": alert["tenant_id"],
            "vehicle_id": alert["vehicle_id"],
            "fingerprint": alert["fingerprint"],
            "rule_code": alert["rule_code"],
            "severity": alert["severity"],
            "failure_mode": alert["failure_mode"],
            "event_ts": alert["event_ts"],
            "detected_at": alert["detected_at"],
            "details": Jsonb(
                {
                    "vin": alert.get("vin"),
                    "event_seq": alert.get("event_seq"),
                    "value": alert.get("value"),
                    "threshold": alert.get("threshold"),
                    **(alert.get("details") or {}),
                }
            ),
        }

    def _connection(self) -> psycopg.Connection[Any]:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.dsn, connect_timeout=5)
        return self.conn

    def _count_retry(self, attempt: int, exc: BaseException) -> None:
        self.stats["retries"] += 1
        log.warning("postgres unavailable (attempt %d): %s", attempt, exc)
        if self.conn is not None:
            self.conn.close()
        self.conn = None

    def write(self, alerts: list[dict[str, Any]]) -> None:
        if not alerts:
            return
        rows = [(a["status"], self.params(a)) for a in alerts]
        retry(
            lambda: self._write_batch(rows),
            retry_on=(pg_errors.OperationalError, pg_errors.InterfaceError),
            budget_s=self.retry_budget_s,
            on_retry=self._count_retry,
        )

    def _write_batch(self, rows: list[tuple[str, dict[str, Any]]]) -> None:
        conn = self._connection()
        try:
            with conn.transaction(), conn.cursor() as cur:
                for status, params in rows:
                    cur.execute(self._OPEN if status == "open" else self._CLEAR, params)
            self._tally(rows)
        except pg_errors.IntegrityError:
            log.warning("constraint violation in batch; retrying row by row")
            self._write_rows_individually(conn, rows)

    def _write_rows_individually(
        self, conn: psycopg.Connection[Any], rows: list[tuple[str, dict[str, Any]]]
    ) -> None:
        with conn.transaction(), conn.cursor() as cur:
            for status, params in rows:
                try:
                    with conn.transaction():  # savepoint
                        cur.execute(self._OPEN if status == "open" else self._CLEAR, params)
                    self._tally([(status, params)])
                except pg_errors.IntegrityError as exc:
                    self.stats["rejected"] += 1
                    log.error("alert rejected (%s): %s", type(exc).__name__, params["fingerprint"])

    def _tally(self, rows: list[tuple[str, dict[str, Any]]]) -> None:
        for status, _ in rows:
            self.stats["opened" if status == "open" else "cleared"] += 1

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


class LiveStateStore:
    def __init__(self, client: redis.Redis, *, retry_budget_s: float = 60.0) -> None:
        self.client = client
        self.retry_budget_s = retry_budget_s
        self.stats = {"states": 0, "published": 0, "retries": 0}

    def write(self, states: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> None:
        if not states and not alerts:
            return

        def _apply() -> None:
            pipe = self.client.pipeline(transaction=False)
            for s in states:
                vid, tid = s["vehicle_id"], s["tenant_id"]
                pipe.set(f"veh:{vid}", orjson.dumps(s), ex=STATE_TTL_S)
                pipe.zadd(f"tenant:{tid}:health", {vid: s["health_score"]})
                if s.get("latitude") is not None and s.get("longitude") is not None:
                    pipe.geoadd(f"tenant:{tid}:geo", (s["longitude"], s["latitude"], vid))
            for a in alerts:
                pipe.publish(f"alerts:{a['tenant_id']}", orjson.dumps(a))
            pipe.execute()

        def _count_retry(attempt: int, exc: BaseException) -> None:
            self.stats["retries"] += 1
            log.warning("redis unavailable (attempt %d): %s", attempt, exc)

        retry(
            _apply,
            retry_on=(redis.ConnectionError, redis.TimeoutError),
            budget_s=self.retry_budget_s,
            on_retry=_count_retry,
        )
        self.stats["states"] += len(states)
        self.stats["published"] += len(alerts)

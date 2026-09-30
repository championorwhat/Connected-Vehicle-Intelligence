"""Live alert feed (WebSocket), radar signals, health and readiness."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Annotated, Any

import clickhouse_connect
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from prognos_api.deps import DbDep, StateDep, authenticate, require, state
from prognos_api.errors import ApiError
from prognos_api.security import Principal

log = logging.getLogger("prognos_api")
router = APIRouter()


@router.websocket("/v1/ws/alerts")
async def alerts_feed(websocket: WebSocket) -> None:
    """Pushes this tenant's alert transitions as they happen (Redis pub/sub alerts:{tenant}).

    Browsers cannot set an Authorization header on a WebSocket, so the token is sent
    as the first message ({"token": "..."}) rather than in the URL, where it would
    end up in proxy and access logs.
    """
    st = state(websocket)
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        principal = authenticate(st, str(first.get("token", "")))
    except (TimeoutError, ApiError, ValueError, WebSocketDisconnect):
        await websocket.close(code=4401, reason="authentication required")
        return
    if not principal.can("alert:read") or principal.tenant_id is None:
        await websocket.close(code=4403, reason="missing permission alert:read")
        return
    pubsub = st.redis.pubsub()
    await pubsub.subscribe(f"alerts:{principal.tenant_id}")
    await websocket.send_json({"type": "subscribed", "tenant_id": principal.tenant_id})

    async def pump() -> None:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                data = message["data"]
                await websocket.send_text(data.decode() if isinstance(data, bytes) else data)

    task = asyncio.create_task(pump())
    try:
        while True:  # the client may send pings; a disconnect ends the loop
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, RedisError):
            await task
        with contextlib.suppress(RedisError):
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]


@router.get("/v1/fleet/signals", tags=["fleet"])
async def signals(
    st: StateDep, conn: DbDep,
    principal: Annotated[Principal, Depends(require("fleet:read"))],
    since_hours: Annotated[int, Query(ge=1, le=24 * 90)] = 72,
) -> dict[str, Any]:  # fmt: skip
    """Emerging-fault signals for vehicle models in this tenant's fleet.

    Signals are computed across all tenants, so absolute counts (which would reveal
    other fleets' sizes) are withheld; rates and significance are returned.
    """
    models = [r["model_code"] for r in await (await conn.execute(
        "SELECT DISTINCT model_code::text AS model_code FROM vehicles WHERE tenant_id = %s",
        (principal.tenant_id,),
    )).fetchall()]  # fmt: skip
    if not models:
        return {"items": []}
    s = st.settings
    try:
        client = await asyncio.to_thread(
            clickhouse_connect.get_client, host=s.clickhouse_host, port=s.clickhouse_port,
            username=s.clickhouse_user, password=s.clickhouse_password, database=s.clickhouse_db,
        )  # fmt: skip
        result = await asyncio.to_thread(
            client.query,
            "SELECT window_start, window_end, level, dtc, oem, model_code,"
            " nullIf(firmware_version, '') AS firmware_version, powertrain, rate, baseline_rate,"
            " rate_ratio, p_adjusted FROM fleet_signals FINAL"
            " WHERE model_code IN %(models)s AND window_end >= now() - toIntervalHour(%(h)s)"
            " ORDER BY window_end DESC, p_adjusted ASC LIMIT 200",
            parameters={"models": models, "h": since_hours},
        )
    except Exception as exc:  # ClickHouse is optional for the rest of the API
        log.warning("signals unavailable: %s", exc)
        raise ApiError(503, "signals store unavailable", code="signals-down") from exc
    return {"items": [dict(zip(result.column_names, row, strict=True))
                      for row in result.result_rows]}  # fmt: skip


@router.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is serving requests."""
    return {"status": "ok"}


@router.get("/readyz", tags=["ops"])
async def readyz(st: StateDep) -> dict[str, Any]:
    """Readiness: PostgreSQL is required; Redis degrades live views only."""
    checks: dict[str, str] = {}
    try:
        async with st.pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"
    try:
        await st.redis.ping()
        checks["redis"] = "ok"
    except RedisError:
        checks["redis"] = "degraded"
    if checks["postgres"] != "ok":
        raise ApiError(503, "not ready", code="not-ready", extra={"checks": checks})
    return {"status": "ready", "checks": checks}

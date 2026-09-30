"""Alerts: list (newest first, keyset) and acknowledge."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from prognos_api import audit, pagination
from prognos_api.deps import TenantDbDep, client_ip, require
from prognos_api.errors import ApiError
from prognos_api.security import Principal

router = APIRouter(tags=["alerts"])

_COLUMNS = """
    alert_id, vehicle_id::text AS vehicle_id, rule_code, severity, failure_mode, status,
    event_ts, detected_at, acknowledged_at, acknowledged_by::text AS acknowledged_by,
    resolved_at, details
"""


@router.get("/v1/alerts")
async def list_alerts(
    conn: TenantDbDep, principal: Annotated[Principal, Depends(require("alert:read"))],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = 50,
    status: Annotated[
        str | None, Query(pattern="^(open|acknowledged|resolved|suppressed)$")
    ] = None,
    severity: Annotated[str | None, Query(pattern="^(info|warning|critical)$")] = None,
    vehicle_id: uuid.UUID | None = None,
    failure_mode: Annotated[str | None, Query(pattern="^[A-Z][A-Z0-9_]+$")] = None,
) -> dict[str, Any]:  # fmt: skip
    after = pagination.decode(cursor, 2)
    sql = f"SELECT {_COLUMNS} FROM alerts WHERE tenant_id = %(tid)s"
    params: dict[str, Any] = {"tid": principal.tenant_id, "limit": limit + 1}
    for name, value in (("status", status), ("severity", severity),
                        ("vehicle_id", vehicle_id), ("failure_mode", failure_mode)):  # fmt: skip
        if value is not None:
            sql += f" AND {name} = %({name})s"
            params[name] = value
    if after:  # row-value comparison: one index seek on (tenant_id, detected_at, alert_id)
        sql += " AND (detected_at, alert_id) < (%(after_ts)s::timestamptz, %(after_id)s)"
        params["after_ts"], params["after_id"] = after
    sql += " ORDER BY detected_at DESC, alert_id DESC LIMIT %(limit)s"
    rows = await (await conn.execute(sql, params)).fetchall()
    return pagination.page(rows, limit, ["detected_at", "alert_id"])


@router.post("/v1/alerts/{alert_id}/acknowledge")
async def acknowledge(
    alert_id: int, request: Request, conn: TenantDbDep,
    principal: Annotated[Principal, Depends(require("alert:ack"))],
) -> dict[str, Any]:  # fmt: skip
    """open -> acknowledged. Repeating it is a no-op; a resolved alert cannot be acknowledged."""
    async with conn.transaction():
        row = await (await conn.execute(
            f"SELECT {_COLUMNS} FROM alerts WHERE alert_id = %s AND tenant_id = %s FOR UPDATE",
            (alert_id, principal.tenant_id),
        )).fetchone()  # fmt: skip
        if row is None:
            raise ApiError(404, "alert not found", code="not-found")
        if row["status"] == "acknowledged":
            return row
        if row["status"] != "open":
            raise ApiError(409, f"alert is {row['status']}", code="invalid-transition")
        row = await (await conn.execute(
            "UPDATE alerts SET status = 'acknowledged',"
            " acknowledged_at = greatest(now(), detected_at),"
            f" acknowledged_by = %s WHERE alert_id = %s RETURNING {_COLUMNS}",
            (principal.user_id, alert_id),
        )).fetchone()  # fmt: skip
        await audit.record(
            conn, tenant_id=principal.tenant_id, actor_id=principal.user_id,
            action="alert.acknowledge", resource_type="alert", resource_id=str(alert_id),
            outcome="success", request_id=getattr(request.state, "request_id", None),
            client_ip=client_ip(request),
        )  # fmt: skip
    assert row is not None
    return row

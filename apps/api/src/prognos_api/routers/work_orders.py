"""Work orders: list, create, and lifecycle transitions (explicit state machine).

    proposed ──schedule──► scheduled ──start──► in_progress ──complete──► completed
        │                     │  ▲                                  ▲
        └──────cancel─────────┴──┴─(reschedule)       scheduled ────┘ (complete)
                    ▼
                cancelled

Planner proposals arrive as `proposed`; a fleet manager schedules or cancels them;
a technician starts and completes them with an outcome. Every transition is
audited. At most one active order per (vehicle, failure mode) is enforced by the
database (partial unique index), so concurrent creates cannot both succeed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from psycopg import errors as pg_errors
from pydantic import BaseModel, Field

from prognos_api import audit, pagination
from prognos_api.deps import DbDep, client_ip, require
from prognos_api.errors import ApiError
from prognos_api.security import Principal

router = APIRouter(tags=["work orders"])

_COLUMNS = """
    work_order_id::text AS work_order_id, vehicle_id::text AS vehicle_id,
    workshop_id::text AS workshop_id, source_alert_id, failure_mode, status,
    failure_probability::float8 AS failure_probability,
    expected_cost_avoided::float8 AS expected_cost_avoided, model_version, scheduled_for,
    created_by::text AS created_by, created_at, completed_at, outcome
"""

# action -> (allowed from, to, permission)
TRANSITIONS: dict[str, tuple[frozenset[str], str, str]] = {
    "schedule": (frozenset({"proposed", "scheduled"}), "scheduled", "work_order:write"),
    "cancel": (frozenset({"proposed", "scheduled"}), "cancelled", "work_order:write"),
    "start": (frozenset({"scheduled"}), "in_progress", "work_order:complete"),
    "complete": (frozenset({"scheduled", "in_progress"}), "completed", "work_order:complete"),
}


class CreateWorkOrder(BaseModel):
    vehicle_id: uuid.UUID
    failure_mode: str = Field(pattern="^[A-Z][A-Z0-9_]+$")
    workshop_id: uuid.UUID
    scheduled_for: dt.date | None = None
    source_alert_id: int | None = None


class Schedule(BaseModel):
    scheduled_for: dt.date


class Complete(BaseModel):
    outcome: Literal["fault_confirmed", "no_fault_found", "other"]


@router.get("/v1/work-orders")
async def list_work_orders(
    conn: DbDep, principal: Annotated[Principal, Depends(require("work_order:read"))],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = 50,
    status: Annotated[
        str | None, Query(pattern="^(proposed|scheduled|in_progress|completed|cancelled)$")
    ] = None,
    vehicle_id: uuid.UUID | None = None,
) -> dict[str, Any]:  # fmt: skip
    after = pagination.decode(cursor, 2)
    sql = f"SELECT {_COLUMNS} FROM work_orders WHERE tenant_id = %(tid)s"
    params: dict[str, Any] = {"tid": principal.tenant_id, "limit": limit + 1}
    if status:
        sql += " AND status = %(status)s"
        params["status"] = status
    if vehicle_id:
        sql += " AND vehicle_id = %(vid)s"
        params["vid"] = vehicle_id
    if after:
        sql += " AND (created_at, work_order_id) < (%(a_ts)s::timestamptz, %(a_id)s::uuid)"
        params["a_ts"], params["a_id"] = after
    sql += " ORDER BY created_at DESC, work_order_id DESC LIMIT %(limit)s"
    rows = await (await conn.execute(sql, params)).fetchall()
    return pagination.page(rows, limit, ["created_at", "work_order_id"])


@router.post("/v1/work-orders", status_code=201)
async def create_work_order(
    body: CreateWorkOrder, request: Request, conn: DbDep,
    principal: Annotated[Principal, Depends(require("work_order:write"))],
) -> dict[str, Any]:  # fmt: skip
    status = "scheduled" if body.scheduled_for else "proposed"
    try:
        async with conn.transaction():
            row = await (await conn.execute(
                "INSERT INTO work_orders (tenant_id, vehicle_id, workshop_id, source_alert_id,"
                " failure_mode, status, scheduled_for, created_by)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (principal.tenant_id, body.vehicle_id, body.workshop_id, body.source_alert_id,
                 body.failure_mode, status, body.scheduled_for, principal.user_id),
            )).fetchone()  # fmt: skip
            assert row is not None
            await _audit(conn, request, principal, "work_order.create", row["work_order_id"],
                         {"status": status, "failure_mode": body.failure_mode})  # fmt: skip
    except pg_errors.UniqueViolation as exc:
        raise ApiError(409, "an active work order already exists for this vehicle and "
                       "failure mode", code="duplicate-active-work-order") from exc  # fmt: skip
    except pg_errors.ForeignKeyViolation as exc:
        # Composite (id, tenant_id) keys: another tenant's vehicle/workshop/alert fails here.
        raise ApiError(404, "vehicle, workshop, alert or failure mode not found",
                       code="not-found") from exc  # fmt: skip
    return row


async def _transition(
    work_order_id: uuid.UUID, action: str, request: Request, conn: DbDep,
    principal: Principal, updates: dict[str, Any],
) -> dict[str, Any]:  # fmt: skip
    allowed_from, target, permission = TRANSITIONS[action]
    if not principal.can(permission):  # route-level require() covers the common case
        raise ApiError(403, f"missing permission {permission}", code="forbidden")
    async with conn.transaction():
        row = await (await conn.execute(
            "SELECT status FROM work_orders WHERE work_order_id = %s AND tenant_id = %s"
            " FOR UPDATE", (work_order_id, principal.tenant_id),
        )).fetchone()  # fmt: skip
        if row is None:
            raise ApiError(404, "work order not found", code="not-found")
        if row["status"] not in allowed_from:
            raise ApiError(409, f"cannot {action} a {row['status']} work order",
                           code="invalid-transition")  # fmt: skip
        sets = ", ".join(f"{k} = %({k})s" for k in updates)
        updated = await (await conn.execute(
            f"UPDATE work_orders SET status = %(status)s{', ' + sets if sets else ''}"
            f" WHERE work_order_id = %(id)s RETURNING {_COLUMNS}",
            {"status": target, "id": work_order_id, **updates},
        )).fetchone()  # fmt: skip
        assert updated is not None
        await _audit(conn, request, principal, f"work_order.{action}", str(work_order_id),
                     {"from": row["status"], "to": target})  # fmt: skip
    return updated


async def _audit(
    conn: DbDep, request: Request, principal: Principal, action: str, resource_id: str,
    details: dict[str, Any],
) -> None:  # fmt: skip
    await audit.record(
        conn, tenant_id=principal.tenant_id, actor_id=principal.user_id, action=action,
        resource_type="work_order", resource_id=resource_id, outcome="success",
        request_id=getattr(request.state, "request_id", None), client_ip=client_ip(request),
        details=details,
    )  # fmt: skip


@router.post("/v1/work-orders/{work_order_id}/schedule")
async def schedule(
    work_order_id: uuid.UUID, body: Schedule, request: Request, conn: DbDep,
    principal: Annotated[Principal, Depends(require("work_order:write"))],
) -> dict[str, Any]:  # fmt: skip
    if body.scheduled_for < dt.date.today():
        raise ApiError(422, "scheduled_for is in the past", code="validation")
    return await _transition(work_order_id, "schedule", request, conn, principal,
                             {"scheduled_for": body.scheduled_for})  # fmt: skip


@router.post("/v1/work-orders/{work_order_id}/cancel")
async def cancel(
    work_order_id: uuid.UUID, request: Request, conn: DbDep,
    principal: Annotated[Principal, Depends(require("work_order:write"))],
) -> dict[str, Any]:  # fmt: skip
    return await _transition(work_order_id, "cancel", request, conn, principal, {})


@router.post("/v1/work-orders/{work_order_id}/start")
async def start(
    work_order_id: uuid.UUID, request: Request, conn: DbDep,
    principal: Annotated[Principal, Depends(require("work_order:complete"))],
) -> dict[str, Any]:  # fmt: skip
    return await _transition(work_order_id, "start", request, conn, principal, {})


@router.post("/v1/work-orders/{work_order_id}/complete")
async def complete(
    work_order_id: uuid.UUID, body: Complete, request: Request, conn: DbDep,
    principal: Annotated[Principal, Depends(require("work_order:complete"))],
) -> dict[str, Any]:  # fmt: skip
    updates = {"outcome": body.outcome, "completed_at": dt.datetime.now(dt.UTC)}
    return await _transition(work_order_id, "complete", request, conn, principal, updates)

"""Privacy: right-to-erasure requests (data protection officer only).

The API records and tracks requests; the erasure worker (`prognos-api erasure-worker`)
carries them out across PostgreSQL, ClickHouse and Redis (prognos_api.erasure).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from prognos_api import audit
from prognos_api.deps import TenantDbDep, client_ip, require
from prognos_api.errors import ApiError
from prognos_api.security import Principal

router = APIRouter(tags=["privacy"])
Dpo = Annotated[Principal, Depends(require("privacy:erase"))]

_COLUMNS = """
    request_id::text AS request_id, subject_type, subject_id::text AS subject_id, status,
    requested_by::text AS requested_by, requested_at, completed_at, details
"""
_SUBJECT_TABLE = {"driver": ("drivers", "driver_id"), "vehicle": ("vehicles", "vehicle_id")}


class ErasureRequest(BaseModel):
    subject_type: Literal["driver", "vehicle"]
    subject_id: uuid.UUID
    reason: str = Field(default="", max_length=200)


@router.post("/v1/privacy/erasure-requests", status_code=202)
async def create_erasure_request(
    body: ErasureRequest, request: Request, response: Response, conn: TenantDbDep,
    principal: Dpo,
) -> dict[str, Any]:  # fmt: skip
    """File a request. It is carried out asynchronously; poll it by id.

    Filing again for a subject with an unfinished request returns that request (200).
    """
    table, key = _SUBJECT_TABLE[body.subject_type]
    async with conn.transaction():
        exists = await (await conn.execute(
            f"SELECT 1 FROM {table} WHERE {key} = %s AND tenant_id = %s",
            (body.subject_id, principal.tenant_id),
        )).fetchone()  # fmt: skip
        if exists is None:
            raise ApiError(404, f"{body.subject_type} not found", code="not-found")
        # Serialise concurrent filings for one subject (transaction-scoped lock).
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           (f"erasure:{body.subject_id}",))  # fmt: skip
        open_request = await (await conn.execute(
            f"SELECT {_COLUMNS} FROM erasure_requests WHERE tenant_id = %s AND subject_id = %s"
            " AND status IN ('received', 'in_progress')", (principal.tenant_id, body.subject_id),
        )).fetchone()  # fmt: skip
        if open_request is not None:
            response.status_code = 200
            return open_request
        row = await (await conn.execute(
            "INSERT INTO erasure_requests (tenant_id, subject_type, subject_id, requested_by,"
            f" details) VALUES (%s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
            (principal.tenant_id, body.subject_type, body.subject_id, principal.user_id,
             Jsonb({"reason": body.reason} if body.reason else {})),
        )).fetchone()  # fmt: skip
        assert row is not None
        await audit.record(
            conn, tenant_id=principal.tenant_id, actor_id=principal.user_id,
            action="privacy.erasure_request", resource_type=body.subject_type,
            resource_id=str(body.subject_id), outcome="success",
            request_id=getattr(request.state, "request_id", None), client_ip=client_ip(request),
            details={"erasure_request_id": row["request_id"]},
        )  # fmt: skip
    return row


@router.get("/v1/privacy/erasure-requests")
async def list_erasure_requests(conn: TenantDbDep, principal: Dpo) -> dict[str, Any]:
    rows = await (await conn.execute(
        f"SELECT {_COLUMNS} FROM erasure_requests WHERE tenant_id = %s"
        " ORDER BY requested_at DESC LIMIT 200", (principal.tenant_id,),
    )).fetchall()  # fmt: skip
    return {"items": rows}


@router.get("/v1/privacy/erasure-requests/{request_id}")
async def get_erasure_request(
    request_id: uuid.UUID, conn: TenantDbDep, principal: Dpo
) -> dict[str, Any]:
    row = await (await conn.execute(
        f"SELECT {_COLUMNS} FROM erasure_requests WHERE request_id = %s AND tenant_id = %s",
        (request_id, principal.tenant_id),
    )).fetchone()  # fmt: skip
    if row is None:
        raise ApiError(404, "erasure request not found", code="not-found")
    return row

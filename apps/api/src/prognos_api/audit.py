"""Append-only audit trail (audit_log): who did what, to which resource, with what outcome."""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

_INSERT = """
    INSERT INTO audit_log (tenant_id, actor_type, actor_id, action, resource_type, resource_id,
                           outcome, request_id, client_ip, details)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


async def record(
    conn: AsyncConnection[Any], *, tenant_id: str | None, actor_id: str | None, action: str,
    resource_type: str, resource_id: str | None, outcome: str, request_id: str | None,
    client_ip: str | None, details: dict[str, Any] | None = None, actor_type: str = "user",
) -> None:  # fmt: skip
    await conn.execute(
        _INSERT,
        (tenant_id, actor_type, actor_id, action, resource_type, resource_id, outcome,
         request_id, client_ip, Jsonb(details or {})),
    )  # fmt: skip

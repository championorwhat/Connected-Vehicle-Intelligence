"""Request dependencies: shared state, authentication, authorisation, rate limiting."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, Request
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from starlette.requests import HTTPConnection

from prognos_api import audit
from prognos_api.config import Settings
from prognos_api.errors import ApiError
from prognos_api.ratelimit import RateLimiter
from prognos_api.security import InvalidToken, Principal, TokenService, principal_from_claims


@dataclass
class AppState:
    settings: Settings
    pool: AsyncConnectionPool[AsyncConnection[dict[str, Any]]]
    redis: aioredis.Redis
    tokens: TokenService
    policy: dict[str, frozenset[str]]  # role -> permissions, loaded from role_permissions
    limiter: RateLimiter
    # One password hash at a time (see routers/auth.check_password); created with the
    # app state inside the running event loop.
    hashing: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))


def state(request: HTTPConnection) -> AppState:
    st: AppState = request.app.state.prognos
    return st


StateDep = Annotated[AppState, Depends(state)]


async def db(st: StateDep) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
    async with st.pool.connection() as conn:
        yield conn


DbDep = Annotated[AsyncConnection[dict[str, Any]], Depends(db)]

TENANT_ROLE = "prognos_tenant"
RESET_SESSION = "RESET ROLE; RESET app.tenant_id"  # RESET ALL does not reset the role


async def reset_session(conn: AsyncConnection[Any]) -> None:
    """Pool `reset` hook: a connection never goes back to the pool as a tenant."""
    await conn.execute(RESET_SESSION)


def client_ip(request: HTTPConnection) -> str | None:
    """Peer address if it is a valid IP (unix sockets and test clients are not)."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "missing bearer token", code="unauthenticated",
                       headers={"WWW-Authenticate": 'Bearer realm="prognos"'})  # fmt: skip
    return token


def authenticate(st: AppState, token: str) -> Principal:
    try:
        claims = st.tokens.verify(token)
    except InvalidToken as exc:
        raise ApiError(
            401, "invalid or expired token", code="unauthenticated",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc  # fmt: skip
    return principal_from_claims(claims, st.policy)


async def ensure_policy(st: AppState) -> None:
    """Reload the role policy if it was empty at startup (API started before the seed ran)."""
    if not st.policy:
        st.policy = await load_policy(st.pool)


async def current_principal(request: Request, st: StateDep) -> Principal:
    await ensure_policy(st)
    principal = authenticate(st, bearer_token(request))
    request.state.principal = principal
    allowed, retry = await st.limiter.hit(f"user:{principal.user_id}",
                                          st.settings.rate_limit_per_minute)  # fmt: skip
    if not allowed:
        raise ApiError(429, "rate limit exceeded", code="rate-limited",
                       headers={"Retry-After": str(max(retry, 1))})  # fmt: skip
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


async def tenant_db(
    st: StateDep, principal: PrincipalDep
) -> AsyncIterator[AsyncConnection[dict[str, Any]]]:
    """A connection bound to the caller's tenant by PostgreSQL row-level security.

    Queries still filter by tenant; RLS is the second barrier (ADR-010). Callers without
    a tenant (platform staff) get NULL, which matches no row.
    """
    async with st.pool.connection() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', %s, false), set_config('role', %s, false)",
            (principal.tenant_id or "", TENANT_ROLE),
        )
        yield conn


TenantDbDep = Annotated[AsyncConnection[dict[str, Any]], Depends(tenant_db)]


def require(permission: str) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller must hold `permission` and belong to a tenant.

    Denials are audited (who tried what), then answered with 403.
    """

    async def _check(request: Request, principal: PrincipalDep, st: StateDep) -> Principal:
        if principal.can(permission) and principal.tenant_id is not None:
            return principal
        async with st.pool.connection() as conn:
            await audit.record(
                conn, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="access.deny", resource_type="endpoint", resource_id=request.url.path,
                outcome="denied", request_id=getattr(request.state, "request_id", None),
                client_ip=client_ip(request), details={"permission": permission},
            )  # fmt: skip
        raise ApiError(403, f"missing permission {permission}", code="forbidden")

    return _check


async def load_policy(pool: AsyncConnectionPool[Any]) -> dict[str, frozenset[str]]:
    async with pool.connection() as conn:
        rows = await (await conn.execute("SELECT role_code, permission FROM role_permissions")
                      ).fetchall()  # fmt: skip
    policy: dict[str, set[str]] = {}
    for row in rows:
        policy.setdefault(row["role_code"], set()).add(row["permission"])
    return {role: frozenset(perms) for role, perms in policy.items()}

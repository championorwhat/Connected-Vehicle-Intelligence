"""Login (OAuth2 password grant), current user, public signing keys."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request

from prognos_api import audit
from prognos_api.deps import DbDep, PrincipalDep, StateDep, client_ip
from prognos_api.errors import ApiError
from prognos_api.security import verify_password

router = APIRouter()

_USER = """
    SELECT u.user_id::text, u.tenant_id::text, u.password_hash, u.is_active,
           coalesce(array_agg(r.role_code) FILTER (WHERE r.role_code IS NOT NULL), '{}') AS roles
    FROM users u LEFT JOIN user_roles r USING (user_id)
    WHERE u.email = %s
    GROUP BY u.user_id
"""


@router.post("/v1/auth/token", tags=["auth"])
async def token(
    request: Request, st: StateDep, conn: DbDep,
    username: Annotated[str, Form(max_length=254)],
    password: Annotated[str, Form(max_length=1024)],
    grant_type: Annotated[str, Form()] = "password",
) -> dict[str, Any]:  # fmt: skip
    """Exchange email + password for a short-lived access token (Bearer, RS256)."""
    if grant_type != "password":
        raise ApiError(400, "unsupported grant_type", code="unsupported-grant")
    ip = client_ip(request)
    # Brute-force guard: only *failed* attempts count, per client address + account, so a
    # user who logs in successfully many times is never locked out.
    limit_key = f"login:{ip}:{username.lower()}"
    if await st.limiter.count(limit_key) >= st.settings.login_attempts_per_minute:
        raise ApiError(429, "too many failed login attempts; try again shortly",
                       code="rate-limited", headers={"Retry-After": "60"})  # fmt: skip
    row = await (await conn.execute(_USER, (username,))).fetchone()
    ok = verify_password(row["password_hash"] if row else None, password)
    rid = getattr(request.state, "request_id", None)
    if not ok or row is None or not row["is_active"]:
        await st.limiter.hit(limit_key, st.settings.login_attempts_per_minute)
        await audit.record(
            conn, tenant_id=row["tenant_id"] if row else None,
            actor_id=row["user_id"] if row else None, action="auth.login",
            resource_type="user", resource_id=row["user_id"] if row else None,
            outcome="denied", request_id=rid, client_ip=ip,
            details={"reason": "inactive" if row and ok else "bad_credentials"},
        )  # fmt: skip
        # One message for every failure: do not reveal whether the account exists.
        raise ApiError(401, "invalid credentials", code="invalid-credentials")
    access, ttl = st.tokens.issue(row["user_id"], row["tenant_id"], list(row["roles"]))
    await conn.execute("UPDATE users SET last_login_at = now() WHERE user_id = %s",
                       (row["user_id"],))  # fmt: skip
    await audit.record(
        conn, tenant_id=row["tenant_id"], actor_id=row["user_id"], action="auth.login",
        resource_type="user", resource_id=row["user_id"], outcome="success", request_id=rid,
        client_ip=ip,
    )  # fmt: skip
    return {"access_token": access, "token_type": "bearer", "expires_in": ttl}


@router.get("/v1/auth/me", tags=["auth"])
async def me(principal: PrincipalDep) -> dict[str, Any]:
    return {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "roles": sorted(principal.roles),
        "permissions": sorted(principal.permissions),
    }


@router.get("/.well-known/jwks.json", tags=["auth"])
async def jwks(st: StateDep) -> dict[str, Any]:
    return st.tokens.jwks()

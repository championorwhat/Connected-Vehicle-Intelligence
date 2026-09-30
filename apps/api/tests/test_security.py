"""Security primitives, production config guard and pagination (no Docker needed)."""

from __future__ import annotations

import pytest

from prognos_api import pagination
from prognos_api.config import DEFAULT_SECRET, Settings
from prognos_api.errors import ApiError
from prognos_api.security import (
    InvalidToken,
    TokenService,
    hash_password,
    principal_from_claims,
    verify_password,
)


def tokens(ttl: int = 15) -> TokenService:
    return TokenService(None, key_id="k1", issuer="prognos", audience="prognos-api",
                        ttl_minutes=ttl)  # fmt: skip


def test_password_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("a long enough password")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "a long enough password")
    assert not verify_password(h, "wrong")
    assert not verify_password(None, "timing-equaliser")  # unknown user never succeeds
    assert not verify_password("not-a-hash", "x")


def test_token_round_trip_and_claims() -> None:
    svc = tokens()
    token, ttl = svc.issue("u1", "t1", ["fleet_manager"])
    claims = svc.verify(token)
    assert (claims["sub"], claims["tid"], claims["roles"], ttl) == (
        "u1",
        "t1",
        ["fleet_manager"],
        900,
    )


def test_token_from_another_key_or_expired_is_rejected() -> None:
    token, _ = tokens().issue("u1", "t1", [])
    with pytest.raises(InvalidToken):
        tokens().verify(token)  # different (ephemeral) key
    svc = tokens(ttl=-1)
    expired, _ = svc.issue("u1", "t1", [])
    with pytest.raises(InvalidToken):
        svc.verify(expired)


def test_permissions_come_from_server_policy_not_the_token() -> None:
    policy = {"analyst": frozenset({"vehicle:read"})}
    claims = {"sub": "u", "tid": "t", "roles": ["analyst"], "jti": "j",
              "permissions": ["work_order:write"]}  # a forged claim is ignored  # fmt: skip
    p = principal_from_claims(claims, policy)
    assert p.permissions == frozenset({"vehicle:read"})
    assert not p.can("work_order:write")


def test_jwks_exposes_public_key_only() -> None:
    key = tokens().jwks()["keys"][0]
    assert set(key) == {"kty", "use", "alg", "kid", "n", "e"}


def test_production_refuses_development_defaults() -> None:
    Settings(env="development").validate()  # fine locally
    unsafe = Settings(env="production", postgres_dsn=f"password={DEFAULT_SECRET}",
                      cors_origins=("*",))  # fmt: skip
    with pytest.raises(RuntimeError) as exc:
        unsafe.validate()
    message = str(exc.value)
    assert "JWT_PRIVATE_KEY" in message
    assert "passwords" in message
    assert "CORS" in message


def test_cursor_round_trip_and_tampering() -> None:
    rows = [{"id": i} for i in range(4)]
    page = pagination.page(rows, 3, ["id"])
    assert [r["id"] for r in page["items"]] == [0, 1, 2]
    assert pagination.decode(page["next_cursor"], 1) == [2]
    assert pagination.page(rows[:2], 3, ["id"])["next_cursor"] is None
    for bad in ("%%%", pagination.encode([1, 2])):
        with pytest.raises(ApiError):
            pagination.decode(bad, 1)


def test_empty_role_policy_is_reloaded_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """An API started before the RBAC seed must not deny every permission until restarted."""
    import asyncio
    from types import SimpleNamespace

    from prognos_api import deps

    calls: list[object] = []

    async def fake_load(pool: object) -> dict[str, frozenset[str]]:
        calls.append(pool)
        return {"analyst": frozenset({"vehicle:read"})}

    monkeypatch.setattr(deps, "load_policy", fake_load)
    st = SimpleNamespace(pool="pool", policy={})
    asyncio.run(deps.ensure_policy(st))  # type: ignore[arg-type]
    assert st.policy == {"analyst": frozenset({"vehicle:read"})}
    asyncio.run(deps.ensure_policy(st))  # type: ignore[arg-type]
    assert calls == ["pool"]  # a loaded policy is not re-queried per request

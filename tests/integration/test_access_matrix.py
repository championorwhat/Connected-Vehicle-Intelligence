"""Access matrix: every role against every endpoint, checked against the written policy.

`REQUIRED` is the specification (docs/security/threat-model.md, Elevation of privilege).
The test fails if a route exists that the table does not list, so a new endpoint cannot
ship without a deliberate access decision. It also checks the security headers on
every response, including errors.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from prognos_api.config import Settings
from prognos_api.main import SECURITY_HEADERS, create_app, create_user

PASSWORD = "correct-horse-battery-staple"
ANY = str(uuid.uuid4())

# (method, path) -> permission needed; None = any signed-in user; "public" = no token.
REQUIRED: dict[tuple[str, str], str | None] = {
    ("GET", "/v1/auth/me"): None,
    ("GET", "/v1/vehicles"): "vehicle:read",
    ("GET", "/v1/vehicles/at-risk"): "vehicle:read",
    ("GET", f"/v1/vehicles/{ANY}"): "vehicle:read",
    ("GET", "/v1/alerts"): "alert:read",
    ("POST", "/v1/alerts/1/acknowledge"): "alert:ack",
    ("GET", "/v1/work-orders"): "work_order:read",
    ("POST", "/v1/work-orders"): "work_order:write",
    ("POST", f"/v1/work-orders/{ANY}/schedule"): "work_order:write",
    ("POST", f"/v1/work-orders/{ANY}/cancel"): "work_order:write",
    ("POST", f"/v1/work-orders/{ANY}/start"): "work_order:complete",
    ("POST", f"/v1/work-orders/{ANY}/complete"): "work_order:complete",
    ("GET", "/v1/fleet/summary"): "fleet:read",
    ("GET", "/v1/fleet/signals"): "fleet:read",
    ("POST", "/v1/privacy/erasure-requests"): "privacy:erase",
    ("GET", "/v1/privacy/erasure-requests"): "privacy:erase",
    ("GET", f"/v1/privacy/erasure-requests/{ANY}"): "privacy:erase",
}
PUBLIC = {("POST", "/v1/auth/token"), ("GET", "/.well-known/jwks.json"), ("GET", "/healthz"),
          ("GET", "/readyz"), ("GET", "/metrics")}  # fmt: skip
WEBSOCKET = {"/v1/ws/alerts"}  # authenticates with the first message (test_api.py)
ROLES = ["platform_admin", "fleet_manager", "technician", "analyst", "dpo"]


@pytest.fixture(scope="module")
def setup(seeded_dsn: str, redis_url: tuple[str, int]) -> Iterator[tuple[TestClient, Any]]:
    with psycopg.connect(seeded_dsn) as conn:
        slug = conn.execute("SELECT slug FROM tenants ORDER BY slug LIMIT 1").fetchone()[0]  # type: ignore[index]
        policy: dict[str, set[str]] = {}
        for role, perm in conn.execute("SELECT role_code, permission FROM role_permissions"):
            policy.setdefault(role, set()).add(perm)
    for role in ROLES:
        tenant = None if role == "platform_admin" else slug
        asyncio.run(create_user(seeded_dsn, f"{role}@matrix.test", tenant, [role], role, PASSWORD))
    settings = Settings(postgres_dsn=seeded_dsn, redis_host=redis_url[0], redis_port=redis_url[1],
                        rate_limit_per_minute=10_000)  # fmt: skip
    with TestClient(create_app(settings)) as client:
        yield client, policy


def token(client: TestClient, role: str) -> dict[str, str]:
    r = client.post("/v1/auth/token", data={"username": f"{role}@matrix.test",
                                            "password": PASSWORD})  # fmt: skip
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def normalise(path: str) -> str:
    return path.replace(ANY, "{id}").replace("/1/", "/{id}/")


def test_the_matrix_covers_every_route(setup: tuple[TestClient, Any]) -> None:
    client, _ = setup
    app: Any = client.app
    # Every HTTP route, as clients see it (/metrics is hidden from the schema on purpose).
    routes = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    routes.add(("GET", "/metrics"))
    specified = {(m, normalise(p)) for m, p in REQUIRED} | PUBLIC

    def generic(path: str) -> str:
        return "/".join("{id}" if part.startswith("{") else part for part in path.split("/"))

    assert {(m, generic(p)) for m, p in routes} == {(m, generic(p)) for m, p in specified}
    # WebSockets are not in OpenAPI: walk the routers (FastAPI wraps included ones).
    stack, ws = list(app.routes), set()
    while stack:
        route = stack.pop()
        stack.extend(getattr(getattr(route, "original_router", None), "routes", []))
        if type(route).__name__ == "APIWebSocketRoute":
            ws.add(route.path)
    assert ws == WEBSOCKET


@pytest.mark.parametrize("role", ROLES)
def test_each_role_gets_exactly_what_the_policy_grants(
    setup: tuple[TestClient, Any], role: str
) -> None:
    client, policy = setup
    headers = token(client, role)
    has_tenant = role != "platform_admin"
    wrong: list[str] = []
    for (method, path), needed in REQUIRED.items():
        r = client.request(method, path, headers=headers, json={})
        allowed = needed is None or (has_tenant and needed in policy.get(role, set()))
        denied = r.status_code == 403
        if allowed == denied or r.status_code in (401, 500):
            wrong.append(f"{method} {path}: {r.status_code} (allowed={allowed})")
        missing = [h for h in SECURITY_HEADERS if h not in r.headers]
        if missing:
            wrong.append(f"{method} {path}: missing headers {missing}")
    assert not wrong, "\n".join(wrong)


def test_no_token_means_401_everywhere_except_public_routes(
    setup: tuple[TestClient, Any],
) -> None:
    client, _ = setup
    for method, path in REQUIRED:
        r = client.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.headers["www-authenticate"].startswith("Bearer")
        assert all(h in r.headers for h in SECURITY_HEADERS)

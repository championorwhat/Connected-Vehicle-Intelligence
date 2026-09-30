"""API against real PostgreSQL (seeded) and Redis: auth, RBAC, tenant isolation, lifecycle."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import orjson
import psycopg
import pytest
import redis as redis_lib
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from prognos_api.config import Settings
from prognos_api.main import create_app, create_user

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="module")
def world(seeded_dsn: str) -> dict[str, Any]:
    """Two tenants with users in each role, plus a platform admin."""
    with psycopg.connect(seeded_dsn) as conn:
        tenants = conn.execute("SELECT tenant_id::text, slug FROM tenants ORDER BY slug").fetchall()
        (a_id, a_slug), (b_id, b_slug) = tenants[0], tenants[1]
        vehicles = {
            t: [r[0] for r in conn.execute(
                "SELECT vehicle_id::text FROM vehicles WHERE tenant_id = %s ORDER BY vehicle_id",
                (t,),
            )]
            for t in (a_id, b_id)
        }  # fmt: skip
        workshops = {
            t: conn.execute("SELECT workshop_id::text FROM workshops WHERE tenant_id = %s LIMIT 1",
                            (t,)).fetchone()[0]  # type: ignore[index]
            for t in (a_id, b_id)
        }  # fmt: skip
    users = {
        "manager_a": (a_slug, "fleet_manager"), "analyst_a": (a_slug, "analyst"),
        "tech_a": (a_slug, "technician"), "manager_b": (b_slug, "fleet_manager"),
        "admin": (None, "platform_admin"),
    }  # fmt: skip
    for name, (slug, role) in users.items():
        asyncio.run(create_user(seeded_dsn, f"{name}@example.test", slug, [role], name, PASSWORD))
    return {"a": a_id, "b": b_id, "vehicles": vehicles, "workshops": workshops}


@pytest.fixture(scope="module")
def client(
    seeded_dsn: str, redis_url: tuple[str, int], world: dict[str, Any]
) -> Iterator[TestClient]:
    host, port = redis_url
    settings = Settings(postgres_dsn=seeded_dsn, redis_host=host, redis_port=port,
                        rate_limit_per_minute=10_000, login_attempts_per_minute=5)  # fmt: skip
    with TestClient(create_app(settings)) as c:
        yield c


def login(client: TestClient, name: str) -> dict[str, str]:
    r = client.post("/v1/auth/token",
                    data={"username": f"{name}@example.test", "password": PASSWORD})  # fmt: skip
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def problem(r: Any, status: int) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = r.json()
    assert body["status"] == status
    assert body["request_id"]
    return body


# --------------------------------------------------------------------------- auth
def test_login_me_and_jwks(client: TestClient) -> None:
    me = client.get("/v1/auth/me", headers=login(client, "manager_a")).json()
    assert me["roles"] == ["fleet_manager"]
    assert "work_order:write" in me["permissions"]
    keys = client.get("/.well-known/jwks.json").json()["keys"]
    assert keys[0]["alg"] == "RS256"
    assert "d" not in keys[0]  # never the private part


def test_wrong_password_and_unknown_user_look_identical(
    client: TestClient, seeded_dsn: str
) -> None:
    wrong = client.post("/v1/auth/token", data={"username": "analyst_a@example.test",
                                                "password": "not-the-password"})  # fmt: skip
    unknown = client.post("/v1/auth/token", data={"username": "nobody@example.test",
                                                  "password": "not-the-password"})  # fmt: skip
    assert problem(wrong, 401)["detail"] == problem(unknown, 401)["detail"]
    with psycopg.connect(seeded_dsn) as conn:
        denied = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'auth.login'"
                              " AND outcome = 'denied'").fetchone()[0]  # type: ignore[index]  # fmt: skip
    assert denied >= 2


def test_login_is_rate_limited_per_account_and_address(client: TestClient) -> None:
    codes = [
        client.post(
            "/v1/auth/token", data={"username": "probe@example.test", "password": "guess"}
        ).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


@pytest.mark.parametrize("kind", ["missing", "garbage", "other_key", "alg_none", "expired"])
def test_bad_tokens_are_rejected(client: TestClient, kind: str) -> None:
    now = int(time.time())
    claims = {"iss": "prognos", "aud": "prognos-api", "sub": str(uuid.uuid4()), "tid": None,
              "roles": ["fleet_manager"], "iat": now, "exp": now + 60, "jti": "x"}  # fmt: skip
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = {
        "missing": None,
        "garbage": "not.a.jwt",
        "other_key": jwt.encode(claims, other, algorithm="RS256"),
        "alg_none": jwt.encode(claims, None, algorithm="none"),  # type: ignore[arg-type]
        "expired": jwt.encode(claims | {"exp": now - 120}, other, algorithm="RS256"),
    }[kind]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    problem(client.get("/v1/auth/me", headers=headers), 401)


# --------------------------------------------------------------------------- tenancy
def test_vehicle_pages_cover_exactly_the_tenant_fleet(
    client: TestClient, world: dict[str, Any]
) -> None:
    h = login(client, "analyst_a")
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 97}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/v1/vehicles", headers=h, params=params).json()
        seen += [v["vehicle_id"] for v in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == world["vehicles"][world["a"]]  # complete, ordered, no duplicates


def test_other_tenants_vehicle_is_not_found(client: TestClient, world: dict[str, Any]) -> None:
    other = world["vehicles"][world["b"]][0]
    problem(client.get(f"/v1/vehicles/{other}", headers=login(client, "manager_a")), 404)
    assert (
        client.get(f"/v1/vehicles/{other}", headers=login(client, "manager_b")).status_code == 200
    )


def test_platform_admin_has_no_fleet_access(client: TestClient) -> None:
    problem(client.get("/v1/vehicles", headers=login(client, "admin")), 403)


def test_location_is_masked_without_precise_permission(
    client: TestClient, world: dict[str, Any], redis_url: tuple[str, int]
) -> None:
    vid = world["vehicles"][world["a"]][1]
    r = redis_lib.Redis(host=redis_url[0], port=redis_url[1])
    state = {
        "vehicle_id": vid, "tenant_id": world["a"], "latitude": 13.082680,
        "longitude": 80.270718, "health_score": 60, "active_alerts": ["COOLANT_DRIFT"],
    }  # fmt: skip
    r.set(f"veh:{vid}", orjson.dumps(state))
    precise = client.get(f"/v1/vehicles/{vid}", headers=login(client, "manager_a")).json()
    masked = client.get(f"/v1/vehicles/{vid}", headers=login(client, "analyst_a")).json()
    assert precise["live"]["latitude"] == 13.08268
    assert precise["live"]["location_precision"] == "precise"
    assert masked["live"]["latitude"] == 13.08
    assert masked["live"]["location_precision"] == "approx_1km"


def test_at_risk_uses_model_when_scored_else_rules(
    client: TestClient, world: dict[str, Any], redis_url: tuple[str, int]
) -> None:
    a, vids = world["a"], world["vehicles"][world["a"]]
    r = redis_lib.Redis(host=redis_url[0], port=redis_url[1])
    r.delete(f"tenant:{a}:risk")
    r.zadd(f"tenant:{a}:health", {vids[2]: 20, vids[3]: 90})
    h = login(client, "manager_a")
    body = client.get("/v1/vehicles/at-risk", headers=h, params={"source": "model"}).json()
    assert body["source"] == "rules:health_score"  # no model scores yet
    assert body["fallback"] is True
    assert body["items"][0]["vehicle_id"] == vids[2]
    r.zadd(f"tenant:{a}:risk", {vids[3]: 0.91, vids[2]: 0.12, str(uuid.uuid4()): 0.99})
    default = client.get("/v1/vehicles/at-risk", headers=h).json()
    assert default["source"] == "rules:health_score"  # shadow mode: rules by default
    assert default["fallback"] is False
    body = client.get("/v1/vehicles/at-risk", headers=h, params={"source": "model"}).json()
    assert body["source"].startswith("model:")
    assert [i["vehicle_id"] for i in body["items"]][:2] == [vids[3], vids[2]]  # unknown id dropped


# --------------------------------------------------------------------------- alerts
def test_acknowledge_alert_rbac_idempotency_and_isolation(
    client: TestClient, world: dict[str, Any], seeded_dsn: str
) -> None:
    vid = world["vehicles"][world["a"]][4]
    with psycopg.connect(seeded_dsn) as conn:
        alert_id = conn.execute(
            "INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity,"
            " failure_mode, event_ts) VALUES (%s, %s, %s, 'COOLANT_OVERHEAT', 'critical',"
            " 'COOLING_FAILURE', now()) RETURNING alert_id",
            (world["a"], vid, f"api-test-{uuid.uuid4().hex}"),
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
    listed = client.get("/v1/alerts", headers=login(client, "analyst_a"),
                        params={"vehicle_id": vid}).json()["items"]  # fmt: skip
    assert alert_id in [a["alert_id"] for a in listed]
    problem(client.post(f"/v1/alerts/{alert_id}/acknowledge", headers=login(client, "tech_a")), 403)
    problem(client.post(f"/v1/alerts/{alert_id}/acknowledge",
                        headers=login(client, "manager_b")), 404)  # fmt: skip
    h = login(client, "manager_a")
    first = client.post(f"/v1/alerts/{alert_id}/acknowledge", headers=h).json()
    again = client.post(f"/v1/alerts/{alert_id}/acknowledge", headers=h).json()
    assert first["status"] == again["status"] == "acknowledged"
    assert first["acknowledged_at"] == again["acknowledged_at"]
    with psycopg.connect(seeded_dsn) as conn:
        denied = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'access.deny'"
                              " AND details->>'permission' = 'alert:ack'").fetchone()[0]  # type: ignore[index]  # fmt: skip
    assert denied >= 1


# --------------------------------------------------------------------------- work orders
def test_work_order_lifecycle(client: TestClient, world: dict[str, Any], seeded_dsn: str) -> None:
    a = world["a"]
    manager, tech = login(client, "manager_a"), login(client, "tech_a")
    body = {"vehicle_id": world["vehicles"][a][5], "failure_mode": "TYRE_SLOW_LEAK",
            "workshop_id": world["workshops"][a]}  # fmt: skip
    problem(client.post("/v1/work-orders", headers=tech, json=body), 403)
    created = client.post("/v1/work-orders", headers=manager, json=body)
    assert created.status_code == 201, created.text
    wo = created.json()
    assert wo["status"] == "proposed"
    problem(client.post("/v1/work-orders", headers=manager, json=body), 409)  # one active only
    foreign = body | {"workshop_id": world["workshops"][world["b"]]}
    problem(client.post("/v1/work-orders", headers=manager,
                        json=foreign | {"failure_mode": "COOLING_FAILURE"}), 404)  # fmt: skip

    wid = wo["work_order_id"]
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    problem(client.post(f"/v1/work-orders/{wid}/start", headers=tech), 409)  # not scheduled yet
    scheduled = client.post(f"/v1/work-orders/{wid}/schedule", headers=manager,
                            json={"scheduled_for": tomorrow}).json()  # fmt: skip
    assert scheduled["status"] == "scheduled"
    assert (
        client.post(f"/v1/work-orders/{wid}/start", headers=tech).json()["status"] == "in_progress"
    )
    done = client.post(f"/v1/work-orders/{wid}/complete", headers=tech,
                       json={"outcome": "fault_confirmed"}).json()  # fmt: skip
    assert done["status"] == "completed"
    assert done["outcome"] == "fault_confirmed"
    problem(client.post(f"/v1/work-orders/{wid}/cancel", headers=manager), 409)
    problem(client.post(f"/v1/work-orders/{wid}/cancel", headers=login(client, "manager_b")), 404)
    with psycopg.connect(seeded_dsn) as conn:
        rows = conn.execute(
            "SELECT action FROM audit_log WHERE resource_id = %s ORDER BY audit_id", (wid,)
        ).fetchall()
        actions = [r[0] for r in rows]
    assert actions == ["work_order.create", "work_order.schedule", "work_order.start",
                       "work_order.complete"]  # fmt: skip


# --------------------------------------------------------------------------- errors, ops, ws
def test_errors_are_problem_json_and_headers_are_set(client: TestClient) -> None:
    h = login(client, "manager_a")
    body = problem(client.get("/v1/vehicles/not-a-uuid", headers=h), 422)
    assert body["errors"]
    problem(client.get("/v1/vehicles", headers=h, params={"cursor": "%%%"}), 400)
    r = client.get("/healthz", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/readyz").json()["checks"]["postgres"] == "ok"
    assert b"prognos_api_requests" in client.get("/metrics").content


def test_websocket_pushes_only_this_tenants_alerts(
    client: TestClient, world: dict[str, Any], redis_url: tuple[str, int]
) -> None:
    token = login(client, "manager_a")["Authorization"].removeprefix("Bearer ")
    r = redis_lib.Redis(host=redis_url[0], port=redis_url[1])
    with client.websocket_connect("/v1/ws/alerts") as ws:
        ws.send_json({"token": token})
        assert ws.receive_json()["type"] == "subscribed"
        for _ in range(50):  # wait until the server-side subscription is live
            if r.pubsub_numsub(f"alerts:{world['a']}")[0][1]:
                break
            time.sleep(0.05)
        r.publish(f"alerts:{world['b']}", orjson.dumps({"rule_code": "OTHER_TENANT"}))
        r.publish(f"alerts:{world['a']}", orjson.dumps({"rule_code": "COOLANT_OVERHEAT"}))
        assert ws.receive_json()["rule_code"] == "COOLANT_OVERHEAT"


def test_websocket_rejects_missing_token(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect("/v1/ws/alerts") as ws:
        ws.send_json({"token": "nope"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401

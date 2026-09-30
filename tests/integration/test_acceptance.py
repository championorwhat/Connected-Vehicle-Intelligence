"""Acceptance scenarios (BDD): features/fleet_manager.feature, run end to end.

Each step drives the real FastAPI app (TestClient), the real planner (plan_once) and a
seeded PostgreSQL + Redis. "acme" and "rival" are the first two seeded tenants.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Iterator
from typing import Any

import orjson
import psycopg
import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from prognos_api.config import Settings
from prognos_api.main import create_app, create_user
from prognos_stream.planner import Calibration
from prognos_stream.planner_main import plan_once

scenarios("features/fleet_manager.feature")

PASSWORD = "correct-horse-battery-staple"
CAL = Calibration.from_dict({
    "version": "bdd", "horizon_hours": 168,
    "fallback": {"p_failure": 0.5, "hours_to_failure_p10": None},
    "rules": {"COOLANT_OVERHEAT": {"p_failure": 0.95, "hours_to_failure_p10": 12.0}},
})  # fmt: skip
USERS = {("acme", "fleet_manager"), ("rival", "fleet_manager"), ("acme", "technician"),
         ("acme", "analyst")}  # fmt: skip


@pytest.fixture(scope="module")
def tenants(seeded_dsn: str) -> dict[str, tuple[str, str]]:
    """name -> (tenant_id, slug); users for every (tenant, role) in USERS."""
    with psycopg.connect(seeded_dsn) as conn:
        rows = conn.execute("SELECT tenant_id::text, slug FROM tenants ORDER BY slug LIMIT 2")
        (a_id, a_slug), (b_id, b_slug) = rows.fetchall()
    names = {"acme": (a_id, a_slug), "rival": (b_id, b_slug)}
    for name, role in USERS:
        asyncio.run(create_user(seeded_dsn, f"bdd-{name}-{role}@bdd.test", names[name][1],
                                [role], f"{name} {role}", PASSWORD))  # fmt: skip
    return names


@pytest.fixture(scope="module")
def client(seeded_dsn: str, redis_url: tuple[str, int], tenants: Any) -> Iterator[TestClient]:
    settings = Settings(postgres_dsn=seeded_dsn, redis_host=redis_url[0],
                        redis_port=redis_url[1], rate_limit_per_minute=10_000)  # fmt: skip
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def ctx(seeded_dsn: str, tenants: dict[str, tuple[str, str]]) -> dict[str, Any]:
    """Per scenario: a fresh acme ICE vehicle that no other test has used."""
    with psycopg.connect(seeded_dsn) as conn:
        vid = conn.execute(
            "SELECT v.vehicle_id::text FROM vehicles v JOIN vehicle_models m USING (model_code)"
            " WHERE v.tenant_id = %s AND v.status = 'active' AND m.powertrain = 'ICE'"
            " AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.vehicle_id = v.vehicle_id)"
            " AND NOT EXISTS (SELECT 1 FROM work_orders w WHERE w.vehicle_id = v.vehicle_id)"
            " ORDER BY random() LIMIT 1", (tenants["acme"][0],),
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
    return {"vehicle": vid}


def token(client: TestClient, name: str, role: str) -> dict[str, str]:
    r = client.post("/v1/auth/token", data={"username": f"bdd-{name}-{role}@bdd.test",
                                            "password": PASSWORD})  # fmt: skip
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def active_orders(client: TestClient, vid: str) -> list[dict[str, Any]]:
    r = client.get("/v1/work-orders", params={"vehicle_id": vid},
                   headers=token(client, "acme", "fleet_manager"))  # fmt: skip
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return [w for w in items if w["status"] in ("proposed", "scheduled", "in_progress")]


# ---------------------------------------------------------------- given
@given(parsers.parse('two fleet customers, "{a}" and "{b}", each with a fleet manager'))
def two_customers(tenants: dict[str, Any], a: str, b: str) -> None:
    assert {a, b} == set(tenants)


@given("acme has a technician and an analyst")
def staff() -> None:
    assert ("acme", "technician") in USERS
    assert ("acme", "analyst") in USERS


@given("acme's vehicle reports a critical coolant overheat")
def coolant_alert(seeded_dsn: str, tenants: Any, ctx: dict[str, Any]) -> None:
    with psycopg.connect(seeded_dsn) as conn:
        conn.execute(
            "INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity,"
            " failure_mode, event_ts) VALUES (%s, %s, %s, 'COOLANT_OVERHEAT', 'critical',"
            " 'COOLING_FAILURE', now() - interval '5 minutes')",
            (tenants["acme"][0], ctx["vehicle"], f"bdd-{ctx['vehicle']}"),
        )


@given(parsers.parse("acme's vehicle is live at {lat:f}, {lon:f}"))
def live_position(redis_url: tuple[str, int], ctx: dict[str, Any], lat: float,
                  lon: float) -> None:  # fmt: skip
    state = {"vehicle_id": ctx["vehicle"], "health_score": 100, "as_of": "2026-09-30T00:00:00Z",
             "latitude": lat, "longitude": lon, "speed_kmh": 42.0, "ignition_on": True}  # fmt: skip
    redis_lib.Redis(host=redis_url[0], port=redis_url[1]).set(
        f"veh:{ctx['vehicle']}", orjson.dumps(state), ex=600
    )


# ---------------------------------------------------------------- when
@when("the planner runs")
@when("the planner runs again")
def planner_runs(seeded_dsn: str) -> None:
    with psycopg.connect(seeded_dsn, autocommit=True) as conn:
        plan_once(conn, CAL)


@when("acme's fleet manager schedules it for tomorrow")
def schedule(client: TestClient, ctx: dict[str, Any]) -> None:
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    r = client.post(f"/v1/work-orders/{ctx['order']}/schedule",
                    json={"scheduled_for": tomorrow.isoformat()},
                    headers=token(client, "acme", "fleet_manager"))  # fmt: skip
    ctx["response"] = r


@when("acme's technician tries to schedule the proposed work order")
def technician_schedules(client: TestClient, ctx: dict[str, Any]) -> None:
    (order,) = active_orders(client, ctx["vehicle"])
    ctx["order"] = order["work_order_id"]
    ctx["response"] = client.post(f"/v1/work-orders/{order['work_order_id']}/schedule",
                                  json={"scheduled_for": dt.date.today().isoformat()},
                                  headers=token(client, "acme", "technician"))  # fmt: skip


@when("rival's fleet manager asks for acme's vehicle")
def rival_asks(client: TestClient, ctx: dict[str, Any]) -> None:
    ctx["response"] = client.get(f"/v1/vehicles/{ctx['vehicle']}",
                                 headers=token(client, "rival", "fleet_manager"))  # fmt: skip


@when(parsers.parse("acme's {role} opens the vehicle"))
def opens_vehicle(client: TestClient, ctx: dict[str, Any], role: str) -> None:
    role = role.replace("fleet manager", "fleet_manager")
    r = client.get(f"/v1/vehicles/{ctx['vehicle']}", headers=token(client, "acme", role))
    assert r.status_code == 200, r.text
    ctx["vehicle_view"] = r.json()


# ---------------------------------------------------------------- then
@then("acme sees a proposed work order for that vehicle's cooling system")
def sees_proposal(client: TestClient, ctx: dict[str, Any]) -> None:
    (order,) = active_orders(client, ctx["vehicle"])
    assert (order["status"], order["failure_mode"]) == ("proposed", "COOLING_FAILURE")
    assert order["expected_cost_avoided"] is None  # placeholder costs never become money
    ctx["order"] = order["work_order_id"]


@then("the work order is scheduled for tomorrow")
def is_scheduled(client: TestClient, ctx: dict[str, Any]) -> None:
    assert ctx["response"].status_code == 200, ctx["response"].text
    (order,) = active_orders(client, ctx["vehicle"])
    assert order["status"] == "scheduled"
    assert order["scheduled_for"] == (dt.date.today() + dt.timedelta(days=1)).isoformat()


@then("the audit trail records who proposed and who scheduled it")
def audit_trail(seeded_dsn: str, ctx: dict[str, Any]) -> None:
    with psycopg.connect(seeded_dsn) as conn:
        rows = conn.execute(
            "SELECT action, actor_type, actor_id FROM audit_log WHERE resource_id = %s"
            " ORDER BY audit_id", (ctx["order"],),
        ).fetchall()  # fmt: skip
        manager = conn.execute("SELECT user_id::text FROM users WHERE email = %s",
                               ("bdd-acme-fleet_manager@bdd.test",)).fetchone()[0]  # type: ignore[index]  # fmt: skip
    assert rows == [("work_order.propose", "system", "prognos-planner"),
                    ("work_order.schedule", "user", manager)]  # fmt: skip


@then("acme has exactly one active work order for that vehicle's cooling system")
def exactly_one(client: TestClient, ctx: dict[str, Any]) -> None:
    orders = active_orders(client, ctx["vehicle"])
    assert [o["failure_mode"] for o in orders] == ["COOLING_FAILURE"]


@then('the answer is "not found", as if the vehicle did not exist')
def not_found(ctx: dict[str, Any]) -> None:
    r = ctx["response"]
    assert r.status_code == 404
    assert r.json()["detail"] == "vehicle not found"


@then("the request is refused as forbidden")
def forbidden(ctx: dict[str, Any]) -> None:
    assert ctx["response"].status_code == 403


@then("the refusal is recorded in the audit trail")
def refusal_audited(seeded_dsn: str, ctx: dict[str, Any]) -> None:
    with psycopg.connect(seeded_dsn) as conn:
        row = conn.execute(
            "SELECT a.outcome, a.details ->> 'permission' FROM audit_log a"
            " JOIN users u ON u.user_id::text = a.actor_id"
            " WHERE a.action = 'access.deny' AND u.email = 'bdd-acme-technician@bdd.test'"
            " AND a.resource_id LIKE %s ORDER BY a.audit_id DESC LIMIT 1",
            (f"%{ctx['order']}%",),
        ).fetchone()
    assert row == ("denied", "work_order:write")


@then(parsers.parse('its position is {lat:f}, {lon:f} with precision "{precision}"'))
def position(ctx: dict[str, Any], lat: float, lon: float, precision: str) -> None:
    live = ctx["vehicle_view"]["live"]
    assert (live["latitude"], live["longitude"], live["location_precision"]) == (
        lat, lon, precision)  # fmt: skip

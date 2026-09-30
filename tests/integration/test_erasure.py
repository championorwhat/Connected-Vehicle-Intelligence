"""Right to erasure end to end: DPO files a request, the worker erases across all stores."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import redis as redis_lib
from fastapi.testclient import TestClient

from prognos_api import erasure
from prognos_api.config import Settings
from prognos_api.main import create_app, create_user

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="module")
def setup(seeded_dsn: str) -> dict[str, Any]:
    with psycopg.connect(seeded_dsn) as conn:
        (a, a_slug), (b, _) = conn.execute(
            "SELECT tenant_id::text, slug FROM tenants ORDER BY slug LIMIT 2"
        ).fetchall()
        # A vehicle and a driver that are linked, so erasing either breaks the link.
        vehicle, driver = conn.execute(
            "SELECT vehicle_id::text, driver_id::text FROM driver_assignments"
            " WHERE tenant_id = %s ORDER BY vehicle_id LIMIT 1", (a,),
        ).fetchone()  # type: ignore[misc]  # fmt: skip
        other_vehicle = conn.execute(
            "SELECT vehicle_id::text FROM vehicles WHERE tenant_id = %s AND vehicle_id <> %s"
            " LIMIT 1", (a, vehicle),
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
        b_vehicle = conn.execute("SELECT vehicle_id::text FROM vehicles WHERE tenant_id = %s"
                                 " LIMIT 1", (b,)).fetchone()[0]  # type: ignore[index]  # fmt: skip
    for name, slug, role in (("dpo_a", a_slug, "dpo"), ("boss_a", a_slug, "fleet_manager")):
        asyncio.run(create_user(seeded_dsn, f"{name}@erasure.test", slug, [role], name, PASSWORD))
    return {"a": a, "vehicle": vehicle, "driver": driver, "other_vehicle": other_vehicle,
            "b_vehicle": b_vehicle}  # fmt: skip


@pytest.fixture(scope="module")
def client(seeded_dsn: str, redis_url: tuple[str, int], setup: dict[str, Any]
           ) -> Iterator[TestClient]:  # fmt: skip
    host, port = redis_url
    settings = Settings(postgres_dsn=seeded_dsn, redis_host=host, redis_port=port,
                        rate_limit_per_minute=10_000)  # fmt: skip
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def rds(redis_url: tuple[str, int]) -> redis_lib.Redis:
    return redis_lib.Redis(host=redis_url[0], port=redis_url[1])


def auth(client: TestClient, name: str) -> dict[str, str]:
    r = client.post("/v1/auth/token", data={"username": f"{name}@erasure.test",
                                            "password": PASSWORD})  # fmt: skip
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def add_events(ch: Any, tenant: str, vehicle: str, n: int = 3) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    rows = [[uuid.uuid4(), uuid.UUID(tenant), uuid.UUID(vehicle), "1HGCM82633A004352", "oem_a",
             1, i, now, now, "telemetry", 12.97 + i / 1000, 77.59, 40.0, 0, 1000.0, True, []]
            for i in range(n)]  # fmt: skip
    ch.insert("events", rows, column_names=[
        "event_id", "tenant_id", "vehicle_id", "vin", "oem", "schema_version", "seq",
        "event_ts", "ingest_ts", "event_type", "latitude", "longitude", "speed_kmh",
        "heading_deg", "odometer_km", "ignition_on", "dtc_codes"])  # fmt: skip


def locations(ch: Any, vehicle: str) -> list[tuple[float, float]]:
    return [tuple(r) for r in ch.query(
        "SELECT latitude, longitude FROM events WHERE vehicle_id = %(v)s", {"v": vehicle}
    ).result_rows]  # fmt: skip


def test_only_a_dpo_can_file_and_only_for_their_own_tenant(
    client: TestClient, setup: dict[str, Any]
) -> None:
    body = {"subject_type": "vehicle", "subject_id": setup["vehicle"]}
    manager = client.post("/v1/privacy/erasure-requests", json=body,
                          headers=auth(client, "boss_a"))  # fmt: skip
    assert manager.status_code == 403
    theirs = {"subject_type": "vehicle", "subject_id": setup["b_vehicle"]}
    other = client.post("/v1/privacy/erasure-requests", headers=auth(client, "dpo_a"), json=theirs)
    assert other.status_code == 404  # another tenant's vehicle is indistinguishable from none


def test_vehicle_erasure_removes_location_everywhere_and_keeps_the_rest(
    client: TestClient, setup: dict[str, Any], seeded_dsn: str, ch: Any, rds: redis_lib.Redis
) -> None:
    tid, vid = setup["a"], setup["vehicle"]
    add_events(ch, tid, vid)
    add_events(ch, tid, setup["other_vehicle"])
    rds.set(f"veh:{vid}", b'{"latitude": 12.97, "longitude": 77.59}')
    rds.geoadd(f"tenant:{tid}:geo", (77.59, 12.97, vid))

    headers = auth(client, "dpo_a")
    body = {"subject_type": "vehicle", "subject_id": vid, "reason": "owner request"}
    first = client.post("/v1/privacy/erasure-requests", json=body, headers=headers)
    assert first.status_code == 202, first.text
    again = client.post("/v1/privacy/erasure-requests", json=body, headers=headers)
    assert again.status_code == 200  # idempotent while unfinished
    assert again.json()["request_id"] == first.json()["request_id"]
    assert first.json()["status"] == "received"

    outcomes = erasure.process_pending(seeded_dsn, ch, rds)
    assert [(o.request_id, o.status) for o in outcomes] == [(first.json()["request_id"],
                                                             "completed")]  # fmt: skip

    done = client.get(f"/v1/privacy/erasure-requests/{first.json()['request_id']}",
                      headers=headers).json()  # fmt: skip
    assert done["status"] == "completed"
    assert done["completed_at"]
    assert done["details"]["reason"] == "owner request"
    assert done["details"]["driver_assignments_deleted"] >= 1
    assert "Kafka" in done["details"]["residual"]  # the honest residual window

    erased = locations(ch, vid)
    assert erased
    assert all(lat != lat and lon != lon for lat, lon in erased)  # NaN
    assert all(lat == lat for lat, _ in locations(ch, setup["other_vehicle"]))  # untouched
    assert rds.get(f"veh:{vid}") is None
    assert rds.geopos(f"tenant:{tid}:geo", vid) == [None]
    with psycopg.connect(seeded_dsn) as conn:
        links = conn.execute("SELECT count(*) FROM driver_assignments WHERE vehicle_id = %s",
                             (vid,)).fetchone()[0]  # type: ignore[index]  # fmt: skip
        kept = conn.execute("SELECT count(*) FROM vehicles WHERE vehicle_id = %s",
                            (vid,)).fetchone()[0]  # type: ignore[index]  # fmt: skip
        audited = conn.execute(
            "SELECT array_agg(action ORDER BY audit_id) FROM audit_log WHERE resource_id IN"
            " (%s, %s)", (vid, first.json()["request_id"]),
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
    assert links == 0
    assert kept == 1  # maintenance records stay
    assert audited == ["privacy.erasure_request", "privacy.erasure_execute"]


def test_driver_erasure_unlinks_the_person(
    client: TestClient, setup: dict[str, Any], seeded_dsn: str, ch: Any, rds: redis_lib.Redis
) -> None:
    r = client.post("/v1/privacy/erasure-requests", headers=auth(client, "dpo_a"),
                    json={"subject_type": "driver", "subject_id": setup["driver"]})  # fmt: skip
    assert r.status_code == 202, r.text
    assert [o.status for o in erasure.process_pending(seeded_dsn, ch, rds)] == ["completed"]
    with psycopg.connect(seeded_dsn) as conn:
        erased_at, links = conn.execute(
            "SELECT erased_at, (SELECT count(*) FROM driver_assignments WHERE driver_id = %s)"
            " FROM drivers WHERE driver_id = %s", (setup["driver"], setup["driver"]),
        ).fetchone()  # type: ignore[misc]  # fmt: skip
    assert erased_at is not None
    assert links == 0


class BrokenClickHouse:
    def command(self, cmd: str, *, parameters: Any = None, settings: Any = None) -> Any:
        raise ConnectionError("clickhouse down")


def test_store_outage_returns_the_request_to_the_queue(
    client: TestClient, setup: dict[str, Any], seeded_dsn: str, rds: redis_lib.Redis
) -> None:
    body = {"subject_type": "vehicle", "subject_id": setup["other_vehicle"]}
    r = client.post("/v1/privacy/erasure-requests", headers=auth(client, "dpo_a"), json=body)
    assert r.status_code == 202, r.text
    rds.set(f"veh:{setup['other_vehicle']}", b"{}")
    outcomes = erasure.process_pending(seeded_dsn, BrokenClickHouse(), rds)
    assert [(o.status, o.details["attempts"]) for o in outcomes] == [("received", 1)]
    # Nothing was half-done: PostgreSQL rolled back and Redis was not touched yet.
    assert rds.get(f"veh:{setup['other_vehicle']}") == b"{}"
    status = client.get(f"/v1/privacy/erasure-requests/{r.json()['request_id']}",
                        headers=auth(client, "dpo_a")).json()  # fmt: skip
    assert status["status"] == "received"
    assert status["details"]["last_error"] == "ConnectionError"

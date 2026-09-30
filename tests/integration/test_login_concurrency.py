"""A slow password check must not freeze the rest of the API (M14 load-test finding)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prognos_api import security
from prognos_api.config import Settings
from prognos_api.main import create_app, create_user
from prognos_api.routers import auth

PASSWORD = "correct-horse-battery-staple"
SLOW_S = 0.6


@pytest.fixture(scope="module")
def client(seeded_dsn: str, redis_url: tuple[str, int]) -> Iterator[TestClient]:
    asyncio.run(create_user(seeded_dsn, "slowhash@example.test", None, ["platform_admin"],
                            "slow", PASSWORD))  # fmt: skip
    settings = Settings(postgres_dsn=seeded_dsn, redis_host=redis_url[0],
                        redis_port=redis_url[1])  # fmt: skip
    with TestClient(create_app(settings)) as c:
        yield c


def test_other_requests_are_served_while_a_password_is_being_checked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_verify(password_hash: str | None, password: str) -> bool:
        time.sleep(SLOW_S)  # blocking, like argon2id's CPU work
        return security.verify_password(password_hash, password)

    monkeypatch.setattr(auth, "verify_password", slow_verify)
    login: dict[str, Any] = {}

    def sign_in() -> None:
        started = time.perf_counter()
        form = {"username": "slowhash@example.test", "password": PASSWORD}
        r = client.post("/v1/auth/token", data=form)
        login["status"], login["seconds"] = r.status_code, time.perf_counter() - started

    thread = threading.Thread(target=sign_in)
    thread.start()
    time.sleep(0.15)  # the login is now inside the password check
    started = time.perf_counter()
    assert client.get("/healthz").status_code == 200
    health_s = time.perf_counter() - started
    thread.join()
    assert login["status"] == 200
    assert login["seconds"] >= SLOW_S
    assert health_s < 0.2, f"/healthz waited {health_s:.2f}s behind a password check"


def test_a_login_burst_does_not_starve_the_connection_pool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More logins than pool connections (10): logins wait for their turn to hash, and
    while they wait they must not hold database connections other requests need."""

    def slow_verify(password_hash: str | None, password: str) -> bool:
        time.sleep(0.3)
        return security.verify_password(password_hash, password)

    monkeypatch.setattr(auth, "verify_password", slow_verify)
    form = {"username": "slowhash@example.test", "password": PASSWORD}
    burst = [threading.Thread(target=lambda: client.post("/v1/auth/token", data=form))
             for _ in range(12)]  # fmt: skip
    for t in burst:
        t.start()
    time.sleep(0.5)  # several logins queued behind the password check
    started = time.perf_counter()
    ready = client.get("/readyz")  # needs a pool connection (2 s timeout)
    ready_s = time.perf_counter() - started
    for t in burst:
        t.join()
    assert ready.status_code == 200, ready.text
    assert ready_s < 0.5, f"/readyz waited {ready_s:.2f}s for a database connection"

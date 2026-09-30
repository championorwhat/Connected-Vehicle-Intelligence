"""Real PostgreSQL and ClickHouse via Testcontainers, migrated with the repo's own migrations."""

from __future__ import annotations

import importlib.util
import os
import socket
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import clickhouse_connect
import psycopg
import pytest
from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[attr-defined]
from testcontainers.core.container import DockerContainer
from testcontainers.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parents[2]
PG_MIGRATIONS = ROOT / "database/postgres/migrations"
CH_DIR = ROOT / "database/telemetry"
CH_USER, CH_PASSWORD = "prognos", "test-only"


def migration_sections(path: Path) -> tuple[str, str]:
    """Split a dbmate file into its (up, down) SQL (options such as transaction:false dropped)."""
    text = path.read_text()
    up, down = text.split("-- migrate:down", 1)
    return up.split("-- migrate:up", 1)[1].split("\n", 1)[1], down.split("\n", 1)[1]


def apply_pg(conn: psycopg.Connection, direction: str = "up") -> None:
    files = sorted(PG_MIGRATIONS.glob("*.sql"), reverse=direction == "down")
    for file in files:
        up, down = migration_sections(file)
        if "transaction:false" in file.read_text().split("\n", 1)[0]:
            # dbmate runs these outside a transaction (e.g. CREATE INDEX CONCURRENTLY)
            conn.commit()
            conn.autocommit = True
            conn.execute(up if direction == "up" else down)  # type: ignore[arg-type,unused-ignore]
            conn.autocommit = False
        else:
            conn.execute(up if direction == "up" else down)  # type: ignore[arg-type,unused-ignore]
    conn.commit()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:17-alpine", driver=None) as container:
        yield container


@pytest.fixture(scope="session")
def pg_dsn(pg_container: PostgresContainer) -> str:
    dsn = pg_container.get_connection_url()
    with psycopg.connect(dsn) as conn:
        apply_pg(conn)
    return dsn


@pytest.fixture(scope="session")
def seeded_dsn(pg_dsn: str) -> str:
    spec = importlib.util.spec_from_file_location(
        "load_seed", ROOT / "database/postgres/seeds/load_seed.py"
    )
    assert spec is not None
    assert spec.loader is not None
    load_seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(load_seed)
    assert load_seed.main(["--vehicles", "2000", "--tenants", "5", "--dsn", pg_dsn]) == 0
    return pg_dsn


@pytest.fixture
def db(seeded_dsn: str) -> Iterator[psycopg.Connection]:
    """A connection whose work is rolled back after each test."""
    with psycopg.connect(seeded_dsn) as conn:
        yield conn
        conn.rollback()


@pytest.fixture(scope="session")
def ch_container() -> Iterator[DockerContainer]:
    container = (
        DockerContainer("clickhouse/clickhouse-server:25.8-alpine")
        .with_env("CLICKHOUSE_DB", "telemetry")
        .with_env("CLICKHOUSE_USER", CH_USER)
        .with_env("CLICKHOUSE_PASSWORD", CH_PASSWORD)
        .with_env("CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT", "1")
        .with_volume_mapping(str(CH_DIR), "/db", "ro")
        .with_exposed_ports(8123)
    )
    with container:
        client = None
        for _ in range(60):
            try:
                client = clickhouse_connect.get_client(
                    host=container.get_container_host_ip(),
                    port=int(container.get_exposed_port(8123)),
                    username=CH_USER,
                    password=CH_PASSWORD,
                )
                client.command("SELECT 1")
                break
            except Exception:  # server still starting
                time.sleep(1)
        assert client is not None, "ClickHouse did not start"
        yield container


def run_ch_migrations(container: DockerContainer) -> str:
    result = container.get_wrapped_container().exec_run(
        ["bash", "/db/migrate.sh", "up"],
        environment={
            "CLICKHOUSE_HOST": "localhost",
            "CLICKHOUSE_USER": CH_USER,
            "CLICKHOUSE_PASSWORD": CH_PASSWORD,
            "CLICKHOUSE_DB": "telemetry",
            "MIGRATIONS_DIR": "/db/migrations",
            # Kafka-engine tables connect lazily; schema tests only need them to exist.
            "KAFKA_BROKERS": "localhost:9",
        },
    )
    output: str = result.output.decode()
    assert result.exit_code == 0, output
    return output


@pytest.fixture(scope="session")
def ch(ch_container: DockerContainer):  # type: ignore[no-untyped-def]
    run_ch_migrations(ch_container)
    return clickhouse_connect.get_client(
        host=ch_container.get_container_host_ip(),
        port=int(ch_container.get_exposed_port(8123)),
        username=CH_USER,
        password=CH_PASSWORD,
        database="telemetry",
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    port = _free_port()
    container = (
        DockerContainer("apache/kafka:4.1.0")
        .with_bind_ports(9092, port)
        .with_env("KAFKA_NODE_ID", "1")
        .with_env("KAFKA_PROCESS_ROLES", "broker,controller")
        .with_env("KAFKA_CONTROLLER_QUORUM_VOTERS", "1@localhost:9093")
        .with_env("KAFKA_LISTENERS", "PLAINTEXT://:9092,CONTROLLER://:9093")
        .with_env("KAFKA_ADVERTISED_LISTENERS", f"PLAINTEXT://localhost:{port}")
        .with_env(
            "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT"
        )
        .with_env("KAFKA_CONTROLLER_LISTENER_NAMES", "CONTROLLER")
        .with_env("KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", "1")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1")
    )
    with container:
        bootstrap = f"localhost:{port}"
        admin = AdminClient({"bootstrap.servers": bootstrap})
        for _ in range(60):
            try:
                admin.list_topics(timeout=2)
                break
            except Exception:  # broker still starting
                time.sleep(1)
        topics = ["telemetry.raw", "telemetry.canonical", "telemetry.dlq", "sim.truth"]
        futures = admin.create_topics([NewTopic(t, 3, 1) for t in topics])
        for future in futures.values():
            future.result(timeout=30)
        yield bootstrap


@pytest.fixture
def fresh_topics(kafka_bootstrap: str):  # type: ignore[no-untyped-def]
    """Factory: create uniquely named topics so Kafka tests never read each other's data."""

    def _make(*names: str, partitions: int = 3) -> dict[str, str]:
        suffix = uuid.uuid4().hex[:8]
        mapping = {name: f"{name}.{suffix}" for name in names}
        admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
        futures = admin.create_topics([NewTopic(t, partitions, 1) for t in mapping.values()])
        for future in futures.values():
            future.result(timeout=30)
        return mapping

    return _make


@pytest.fixture(scope="session")
def redis_url() -> Iterator[tuple[str, int]]:
    container = DockerContainer("redis:7.4-alpine").with_exposed_ports(6379)
    with container:
        import redis as redis_lib

        host, port = container.get_container_host_ip(), int(container.get_exposed_port(6379))
        for _ in range(60):
            try:
                redis_lib.Redis(host=host, port=port).ping()
                break
            except Exception:  # still starting
                time.sleep(0.5)
        yield host, port


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.path).replace(os.sep, "/"):
            item.add_marker(pytest.mark.integration)

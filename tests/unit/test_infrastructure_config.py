"""Guards on infrastructure configuration.

These catch mistakes that `docker compose config` accepts but that would break
the laptop budget or the no-data-loss guarantees (missing memory limits,
missing health checks, topics without a partition count).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES: dict[str, dict[str, Any]] = COMPOSE["services"]
ONE_SHOT = {"kafka-init", "migrate-postgres", "migrate-clickhouse"}
STATEFUL = {"kafka", "postgres", "clickhouse", "redis"}
LAPTOP_BUDGET_MB = 5 * 1024  # Docker VM memory on an 8 GB MacBook Air


def _mem_mb(value: str) -> int:
    m = re.fullmatch(r"(\d+)([mg])", value.lower())
    assert m, f"unparseable memory limit {value!r}"
    n, unit = int(m.group(1)), m.group(2)
    return n * 1024 if unit == "g" else n


@pytest.mark.parametrize("name", sorted(set(SERVICES) - ONE_SHOT))
def test_long_running_services_have_memory_limits(name: str) -> None:
    limit = SERVICES[name].get("deploy", {}).get("resources", {}).get("limits", {}).get("memory")
    assert limit, f"{name} has no memory limit"


@pytest.mark.parametrize("name", sorted(STATEFUL))
def test_stateful_services_have_healthchecks_and_volumes(name: str) -> None:
    svc = SERVICES[name]
    assert "healthcheck" in svc, f"{name} needs a healthcheck so dependants can wait on it"
    assert svc.get("volumes"), f"{name} must persist data in a volume"


def test_default_stack_fits_laptop_budget() -> None:
    default = [n for n, s in SERVICES.items() if n not in ONE_SHOT and not s.get("profiles")]
    total = sum(_mem_mb(SERVICES[n]["deploy"]["resources"]["limits"]["memory"]) for n in default)
    assert total <= LAPTOP_BUDGET_MB, (
        f"default stack limit {total} MB exceeds {LAPTOP_BUDGET_MB} MB"
    )


def test_kafka_does_not_auto_create_topics() -> None:
    env = SERVICES["kafka"]["environment"]
    assert env["KAFKA_AUTO_CREATE_TOPICS_ENABLE"] == "false"


def test_redis_never_evicts_state() -> None:
    assert "noeviction" in SERVICES["redis"]["command"]


def _topics() -> list[tuple[str, str, str]]:
    rows = []
    for line in (ROOT / "kafka/topics/topics.conf").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, partitions, configs = (part.strip() for part in line.split("|"))
        rows.append((name, partitions, configs))
    return rows


def test_required_topics_declared() -> None:
    names = {name for name, _, _ in _topics()}
    assert {
        "telemetry.raw",
        "telemetry.canonical",
        "telemetry.dlq",
        "alerts",
        "vehicle.risk",
    } <= names


@pytest.mark.parametrize(("name", "partitions", "configs"), _topics())
def test_topic_rows_are_well_formed(name: str, partitions: str, configs: str) -> None:
    assert re.fullmatch(r"[a-z]+(\.[a-z]+)*", name)
    assert partitions.isdigit() or re.fullmatch(r"\$\{[A-Z_]+\}", partitions)
    for kv in filter(None, configs.split(",")):
        assert "=" in kv, f"{name}: bad config {kv!r}"


def test_compacted_topic_for_latest_risk() -> None:
    configs = {name: cfg for name, _, cfg in _topics()}
    assert "cleanup.policy=compact" in configs["vehicle.risk"]


def test_env_example_covers_compose_variables() -> None:
    compose_text = (ROOT / "docker-compose.yml").read_text()
    used = set(re.findall(r"\$\{([A-Z_]+)", compose_text))
    declared = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert used <= declared, f"undeclared in .env.example: {sorted(used - declared)}"

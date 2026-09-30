"""Compose hardening rules that are easy to regress (M12 threat model, Information disclosure)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FILES = [ROOT / "docker-compose.yml", ROOT / "infra/docker/compose.kafka-ha.yml"]


def services() -> list[tuple[str, str, dict]]:  # type: ignore[type-arg]
    out = []
    for path in FILES:
        for name, svc in (yaml.safe_load(path.read_text()).get("services") or {}).items():
            out.append((path.name, name, svc or {}))
    return out


def test_every_published_port_binds_to_host_bind_not_all_interfaces() -> None:
    """Redis and Kafka have no auth locally: a port on 0.0.0.0 would expose them to the LAN."""
    bad = [
        f"{file}:{name}: {port}"
        for file, name, svc in services()
        for port in svc.get("ports", [])
        if not str(port).startswith("${HOST_BIND:-127.0.0.1}:")
    ]
    assert not bad, bad


def test_no_container_is_privileged_or_shares_the_host_namespaces() -> None:
    bad = [
        f"{file}:{name}"
        for file, name, svc in services()
        if svc.get("privileged") or svc.get("network_mode") == "host" or svc.get("pid") == "host"
    ]
    assert not bad, bad

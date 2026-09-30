"""Normalizer against the real simulator output: every anomaly lands where it should."""

from __future__ import annotations

from collections import Counter

import numpy as np
import orjson
import pytest

from prognos_common.roster import generate_roster
from prognos_common.vin import build_vin
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.formats import MALFORMED_KINDS, corrupt
from prognos_sim.publishers import MemoryPublisher
from prognos_stream.processor import CANONICAL, DLQ, DUPLICATE, Normalizer
from prognos_stream.registry import VehicleRegistry

START = 1_790_000_000.0
ROSTER = generate_roster(600, 4, 42)


def simulate(seconds: float = 10, **overrides: object) -> tuple[ShardSimulator, MemoryPublisher]:
    base: dict[str, object] = {
        "vehicle_count": 600, "events_per_second": 600.0, "tenant_count": 4,
        "mode": Mode.FAST, "publisher": PublisherKind.NULL, "metrics_port": 0,
    }  # fmt: skip
    base.update(overrides)
    cfg = SimConfig(**base)  # type: ignore[arg-type]
    pub = MemoryPublisher()
    sim = ShardSimulator(cfg, 0, ROSTER.vehicles, pub, START)
    for tick in range(int(seconds * 10)):
        sim.tick(START + tick * 0.1, 0.1)
    sim.finish()
    return sim, pub


def normalize_all(pub: MemoryPublisher, normalizer: Normalizer) -> list:  # type: ignore[type-arg]
    outcomes = []
    for topic, _key, value, headers in pub.records:
        if topic != "telemetry.raw":
            continue
        hdrs = {k: v for k, v in headers if isinstance(v, bytes)}
        outcomes.append(normalizer.process(value, hdrs, 0, START + 60))
    return outcomes


@pytest.fixture
def normalizer() -> Normalizer:
    return Normalizer(VehicleRegistry.from_roster(ROSTER))


def test_clean_stream_is_fully_canonical(normalizer: Normalizer) -> None:
    _, pub = simulate(duplicate_rate=0, out_of_order_rate=0, malformed_rate=0, missing_field_rate=0)
    outcomes = normalize_all(pub, normalizer)
    assert Counter(o.kind for o in outcomes) == {CANONICAL: 6_000}
    event = orjson.loads(outcomes[0].value)
    assert event["schema_version"] == 1
    assert event["event_ts"].endswith("Z")
    assert {"vehicle_id", "tenant_id", "event_id", "oem", "tyre_fl_kpa"} <= event.keys()


def test_accounting_and_no_duplicate_forwarded(normalizer: Normalizer) -> None:
    sim, pub = simulate(seconds=20, duplicate_rate=0.03, out_of_order_rate=0.05,
                        malformed_rate=0.02, missing_field_rate=0.02)  # fmt: skip
    outcomes = normalize_all(pub, normalizer)
    kinds = Counter(o.kind for o in outcomes)
    assert sum(kinds.values()) == len(outcomes)
    ids = [orjson.loads(o.value)["event_id"] for o in outcomes if o.kind == CANONICAL]
    assert len(ids) == len(set(ids)), "a duplicate event reached the canonical topic"
    generated = sim.counts["events_generated"]
    # Every unique, well-formed event is forwarded once; only malformed events and those
    # missing a *required* field may be lost to the DLQ.
    assert kinds[CANONICAL] >= generated - sim.counts["malformed"] - sim.counts["missing_field"]
    assert kinds[DLQ] >= sim.counts["malformed"]
    assert kinds[DUPLICATE] > 0
    assert normalizer.counts["late"] > 0


@pytest.mark.parametrize("kind", MALFORMED_KINDS)
def test_each_malformed_kind_goes_to_dlq(normalizer: Normalizer, kind: str) -> None:
    _, pub = simulate(seconds=1, duplicate_rate=0, out_of_order_rate=0, malformed_rate=0,
                      missing_field_rate=0)  # fmt: skip
    rng = np.random.default_rng(0)
    for oem_index, oem in enumerate(("ORION", "VEGA", "LYRA")):
        record = next(r for r in pub.records if dict(r[3]).get("oem") == oem.encode())
        payload = orjson.loads(record[2])
        value, oem_header = corrupt(oem_index, payload, kind, rng)
        headers = {"oem": oem_header.encode()}
        outcome = normalizer.process(value, headers, 0, START + 60)
        assert outcome.kind == DLQ, (oem, kind)
        expected = {
            "truncated": {"malformed_json"}, "not_json": {"malformed_json"},
            "bad_vin": {"invalid_vin"}, "out_of_range": {"out_of_range"},
            "wrong_type": {"invalid_type"}, "bad_timestamp": {"invalid_timestamp"},
            "unknown_oem": {"unknown_oem"},
        }[kind]  # fmt: skip
        assert outcome.reason in expected, (oem, kind, outcome.reason)


def test_unknown_vehicle_and_oem_mismatch(normalizer: Normalizer) -> None:
    _, pub = simulate(seconds=1, duplicate_rate=0, malformed_rate=0, missing_field_rate=0)
    orion = next(r for r in pub.records if dict(r[3]).get("oem") == b"ORION")
    payload = orjson.loads(orion[2])
    payload["vin"] = build_vin("PG1", "CT1A5", 2024, "Z", 999_999)  # valid, never registered
    out = normalizer.process(orjson.dumps(payload), {"oem": b"ORION"}, 0, START + 60)
    assert (out.kind, out.reason) == (DLQ, "unknown_vehicle")
    # A VEGA VIN delivered with ORION headers is a spoofing / routing error.
    vega = next(r for r in pub.records if dict(r[3]).get("oem") == b"VEGA")
    vin = orjson.loads(vega[2])["header"]["vehicleIdentifier"]
    payload["vin"] = vin
    out = normalizer.process(orjson.dumps(payload), {"oem": b"ORION"}, 0, START + 60)
    assert (out.kind, out.reason) == (DLQ, "oem_mismatch")


def test_clock_skew_rules(normalizer: Normalizer) -> None:
    _, pub = simulate(seconds=1, duplicate_rate=0, malformed_rate=0, missing_field_rate=0)
    orion = next(r for r in pub.records if dict(r[3]).get("oem") == b"ORION")
    future = normalizer.process(orion[2], {"oem": b"ORION"}, 0, START - 3_600)
    assert (future.kind, future.reason) == (DLQ, "future_timestamp")
    stale = normalizer.process(orion[2], {"oem": b"ORION"}, 0, START + 30 * 86_400)
    assert (stale.kind, stale.reason) == (DLQ, "stale_event")


def test_empty_and_non_object_payloads(normalizer: Normalizer) -> None:
    assert normalizer.process(b"", {"oem": b"ORION"}, 0, START).reason == "malformed_json"
    assert normalizer.process(b"[1,2]", {"oem": b"ORION"}, 0, START).reason == "malformed_json"
    no_oem = normalizer.process(b"{}", {}, 0, START)
    assert no_oem.reason == "unknown_oem"
    bad_schema = normalizer.process(b"{}", {"oem": b"ORION", "schema": b"orion.v9"}, 0, START)
    assert bad_schema.reason == "unknown_schema_version"


def test_partition_revocation_forgets_windows(normalizer: Normalizer) -> None:
    _, pub = simulate(seconds=1, duplicate_rate=0, malformed_rate=0, missing_field_rate=0)
    record = next(r for r in pub.records if r[0] == "telemetry.raw")
    headers = {k: v for k, v in record[3] if isinstance(v, bytes)}
    assert normalizer.process(record[2], headers, 3, START + 60).kind == CANONICAL
    assert normalizer.process(record[2], headers, 3, START + 60).kind == DUPLICATE
    normalizer.forget_partition(3)
    assert normalizer.process(record[2], headers, 3, START + 60).kind == CANONICAL


def test_normalizer_waits_for_a_seeded_registry() -> None:
    from prognos_stream.main import wait_for_registry

    attempts = iter([VehicleRegistry({}), VehicleRegistry({}), VehicleRegistry.from_roster(ROSTER)])
    registry = wait_for_registry(lambda: next(attempts), timeout_s=5, poll_s=0.01)
    assert len(registry) == 600


def test_normalizer_refuses_to_start_with_empty_registry() -> None:
    from prognos_stream.main import wait_for_registry

    with pytest.raises(RuntimeError):
        wait_for_registry(lambda: VehicleRegistry({}), timeout_s=0.05, poll_s=0.01)

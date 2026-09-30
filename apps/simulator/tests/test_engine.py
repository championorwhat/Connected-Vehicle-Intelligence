"""Event stream properties: rate, formats, anomalies, determinism."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

import orjson
import pytest

from prognos_common.vin import VIN_PATTERN
from prognos_sim.formats import iso_ist, iso_utc

RunFn = Callable[..., Any]


def raw(publisher):  # type: ignore[no-untyped-def]
    return [r for r in publisher.records if r[0] == "telemetry.raw"]


def oem_of(record) -> str:  # type: ignore[no-untyped-def]
    return dict(record[3])["oem"].decode()


def test_rate_is_exact_over_time(run_sim: RunFn) -> None:
    sim, _ = run_sim(seconds=10, events_per_second=1_000)
    assert sim.counts["events_generated"] == 10_000


def test_burst_triples_rate(run_sim: RunFn) -> None:
    sim, _ = run_sim(
        seconds=10, burst_start_seconds=5, burst_duration_seconds=5, burst_multiplier=3
    )
    assert sim.counts["events_generated"] == 5 * 1_000 + 5 * 3_000


def test_every_vehicle_reports_at_one_hertz(run_sim: RunFn) -> None:
    _, pub = run_sim(seconds=5, duplicate_rate=0, out_of_order_rate=0, malformed_rate=0)
    per_vin = Counter(r[1] for r in raw(pub))
    assert set(per_vin.values()) == {5}
    assert len(per_vin) == 1_000


def test_three_oem_formats_are_valid_json(run_sim: RunFn) -> None:
    _, pub = run_sim(seconds=3, malformed_rate=0, missing_field_rate=0)
    by_oem: dict[str, dict] = {}  # type: ignore[type-arg]
    for record in raw(pub):
        by_oem.setdefault(oem_of(record), orjson.loads(record[2]))
    assert set(by_oem) == {"ORION", "VEGA", "LYRA"}

    orion = by_oem["ORION"]
    assert orion["ts"].endswith("Z")
    assert VIN_PATTERN.fullmatch(orion["vin"])
    assert len(orion["tpms_kpa"]) == 4

    vega = by_oem["VEGA"]
    assert vega["header"]["timestamp"].endswith("+05:30")
    assert "speedMph" in vega["motion"]
    assert isinstance(vega["diagnostics"]["activeCodes"], str)

    lyra = by_oem["LYRA"]
    assert isinstance(lyra["timestampMs"], int)
    units = {s["name"]: s["unit"] for s in lyra["signals"]}
    assert units["tyre.fl.pressure"] == "bar"
    assert units["hv.cellDelta"] == "V"
    assert "rpm" not in orjson.dumps(lyra).decode()  # BEVs have no engine signals


def test_unit_conversions_are_consistent(run_sim: RunFn) -> None:
    _, pub = run_sim(seconds=2, malformed_rate=0, missing_field_rate=0)
    for record in raw(pub):
        if oem_of(record) == "VEGA":
            d = orjson.loads(record[2])
            psi = d["tires"]["frontLeftPsi"]
            assert 20 < psi < 110  # 240-620 kPa nominal => ~35-90 psi
            if "engine" in d:
                assert 80 < d["engine"]["coolantTempF"] < 260
        if oem_of(record) == "LYRA":
            d = orjson.loads(record[2])
            bar = next(s["value"] for s in d["signals"] if s["name"] == "tyre.fl.pressure")
            assert 1.5 < bar < 7.5


@pytest.mark.parametrize(
    ("counter", "rate_key", "rate"),
    [
        ("duplicates", "duplicate_rate", 0.05),
        ("out_of_order", "out_of_order_rate", 0.05),
        ("malformed", "malformed_rate", 0.05),
        ("missing_field", "missing_field_rate", 0.05),
    ],
)
def test_anomaly_rates_match_configuration(
    run_sim: RunFn, counter: str, rate_key: str, rate: float
) -> None:
    sim, _ = run_sim(seconds=20, **{rate_key: rate})
    observed = sim.counts[counter] / sim.counts["events_generated"]
    assert abs(observed - rate) < 0.01, observed


def test_message_accounting_balances(run_sim: RunFn) -> None:
    """published = generated + duplicates (after all held-back events are released)."""
    sim, pub = run_sim(seconds=10, duplicate_rate=0.03, out_of_order_rate=0.05)
    assert len(raw(pub)) == sim.counts["events_generated"] + sim.counts["duplicates"]
    assert len(sim.delayed) == 0


def test_out_of_order_arrivals_exist(run_sim: RunFn) -> None:
    _, pub = run_sim(seconds=30, out_of_order_rate=0.05, malformed_rate=0, missing_field_rate=0)
    last_seq: dict[bytes, int] = {}
    regressions = 0
    for record in raw(pub):
        if oem_of(record) != "ORION":
            continue
        seq = orjson.loads(record[2])["seq"]
        if seq < last_seq.get(record[1], -1):
            regressions += 1
        last_seq[record[1]] = max(seq, last_seq.get(record[1], -1))
    assert regressions > 0


def test_duplicates_are_byte_identical(run_sim: RunFn) -> None:
    _, pub = run_sim(seconds=10, duplicate_rate=0.05)
    counts = Counter(r[2] for r in raw(pub))
    assert any(n == 2 for n in counts.values())


def test_all_malformed_kinds_occur(run_sim: RunFn) -> None:
    sim, pub = run_sim(seconds=20, malformed_rate=0.05)
    kinds = {k.removeprefix("malformed_") for k in sim.counts if k.startswith("malformed_")}
    assert kinds == {
        "truncated", "not_json", "bad_vin", "out_of_range", "wrong_type", "bad_timestamp",
        "unknown_oem",
    }  # fmt: skip
    assert any(oem_of(r) == "ZETA" for r in raw(pub))


def test_same_seed_gives_identical_stream(run_sim: RunFn) -> None:
    _, first = run_sim(seconds=3)
    _, second = run_sim(seconds=3)
    assert [r[2] for r in first.records] == [r[2] for r in second.records]


def test_sequence_numbers_increase_per_vehicle(run_sim: RunFn) -> None:
    _, pub = run_sim(seconds=5, duplicate_rate=0, out_of_order_rate=0, malformed_rate=0,
                     missing_field_rate=0)  # fmt: skip
    seqs: dict[bytes, list[int]] = defaultdict(list)
    for record in raw(pub):
        if oem_of(record) == "ORION":
            seqs[record[1]].append(orjson.loads(record[2])["seq"])
    assert all(s == sorted(s) and len(set(s)) == len(s) for s in seqs.values())


def test_timestamp_helpers() -> None:
    assert iso_utc(0.25) == "1970-01-01T00:00:00.250Z"
    assert iso_ist(0.0) == "1970-01-01T05:30:00.000+05:30"

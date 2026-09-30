"""Emerging-fault radar: statistics, cohort baselines, windows, end-to-end scenario."""

from __future__ import annotations

import math

import pytest

from prognos_stream.radar import Cohort, Radar, RadarConfig, poisson_sf

W = 3600.0
FW_OLD = Cohort("LYRA", "EV7V4", "2026.3", "BEV")
FW_NEW = Cohort("LYRA", "EV7V4", "2026.7", "BEV")
OTHER_EV = Cohort("LYRA", "EC8S6", "2026.3", "BEV")
ICE = Cohort("ORION", "CT1A5", "3.1.0", "ICE")


def fleet() -> dict[str, Cohort]:
    cohorts: dict[str, Cohort] = {}
    for prefix, cohort, n in (("old", FW_OLD, 100), ("new", FW_NEW, 100),
                              ("ec8", OTHER_EV, 200), ("ice", ICE, 600)):  # fmt: skip
        cohorts.update({f"{prefix}-{i}": cohort for i in range(n)})
    return cohorts


def radar() -> Radar:
    return Radar(fleet(), RadarConfig(window_s=W, allowed_lateness_s=60))


def test_poisson_tail_matches_closed_forms() -> None:
    assert poisson_sf(0, 3.0) == 1.0
    assert poisson_sf(1, 2.0) == pytest.approx(1 - math.exp(-2.0))
    assert poisson_sf(3, 1.5) == pytest.approx(1 - math.exp(-1.5) * (1 + 1.5 + 1.125))
    tiny = poisson_sf(40, 0.5)  # far tail: no cancellation to zero
    assert 0 < tiny < 1e-40


def test_firmware_defect_fires_at_firmware_and_model_level_only_for_that_cohort() -> None:
    r = radar()
    for i in range(30):  # 30% of the new firmware
        r.add(f"new-{i}", 100.0 + i, ["U0100"])
    r.add("old-1", 200.0, ["U0100"])  # background
    r.add("ec8-1", 200.0, ["U0100"])
    signals = r.advance(W + 61)
    assert {(s["level"], s["model_code"], s["firmware_version"]) for s in signals} == {
        ("firmware", "EV7V4", "2026.7"),
        ("model", "EV7V4", None),
    }
    fw = next(s for s in signals if s["level"] == "firmware")
    assert fw["affected_vehicles"] == 30
    assert fw["cohort_vehicles"] == 100
    assert fw["baseline_affected"] == 1
    assert fw["baseline_vehicles"] == 100
    assert fw["p_adjusted"] < 0.001
    assert fw["rate_ratio"] >= 3


def test_ev_only_code_is_not_anomalous_against_ice_models() -> None:
    """HV codes only exist on EVs: comparing an EV model with ICE vehicles would always fire."""
    r = radar()
    for prefix, n in (("old", 10), ("new", 10), ("ec8", 20)):  # same 10% rate on every EV
        for i in range(n):
            r.add(f"{prefix}-{i}", 10.0, ["P0AFA"])
    assert r.advance(W + 61) == []


def test_one_chatty_vehicle_is_not_a_fleet_pattern() -> None:
    r = radar()
    for i in range(500):
        r.add("new-1", float(i), ["U0100"])
    assert r.advance(W + 61) == []


def test_below_min_vehicles_never_fires() -> None:
    r = radar()
    for i in range(4):
        r.add(f"new-{i}", 5.0, ["P0455"])
    assert r.advance(W + 61) == []


def test_window_closes_only_after_lateness_and_late_events_are_counted() -> None:
    r = radar()
    for i in range(30):
        r.add(f"new-{i}", 10.0, ["U0100"])
    assert r.advance(W + 30) == []  # within the allowed lateness: still open
    assert r.add("new-99", 20.0, ["U0100"]) == []  # late but accepted
    assert r.advance(W + 61)
    r.add("new-98", 30.0, ["U0100"])  # window already closed
    assert r.counts["late_dropped"] == 1


def test_unknown_vehicle_is_counted_not_crashed() -> None:
    r = radar()
    r.add("nobody", 1.0, ["U0100"])
    assert r.counts["unknown_vehicle"] == 1


def test_scenario_end_to_end_detects_defect_and_control_is_silent() -> None:
    from prognos_stream.radar_eval import run, score

    cfg = RadarConfig(window_s=900.0)
    defect = run(400, 0.6, "firmware_defect", 42, cfg, defect_rate=0.01)
    control = run(400, 0.6, "none", 42, cfg, defect_rate=0.01)
    result = score(defect, control)
    assert defect["defect_cohort"]["vehicles"] >= 10
    assert result["defect_detected_at_firmware_level"]
    assert result["other_signals_in_defect_run"] == 0
    assert result["signals_in_control_run"] == 0

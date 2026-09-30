"""Planner: calibrated risk, cost/placeholder economics, capacity-aware greedy scheduling."""

from __future__ import annotations

import pytest

from prognos_stream.planner import (
    Calibration,
    Costs,
    OpenAlert,
    Workshop,
    build_candidates,
    haversine_km,
    schedule,
)

CAL = Calibration.from_dict(
    {
        "version": "test",
        "horizon_hours": 168,
        "fallback": {"p_failure": 0.4, "hours_to_failure_p10": None},
        "rules": {
            "COOLANT_OVERHEAT": {"p_failure": 0.9, "hours_to_failure_p10": 10.0},
            "COOLANT_DRIFT": {"p_failure": 0.6, "hours_to_failure_p10": 30.0},
            "TYRE_SLOW_LEAK": {"p_failure": 0.8, "hours_to_failure_p10": 40.0},
        },
    }
)
PLACEHOLDER = Costs(0, 0, 0, 0, "INR", "PLACEHOLDER - not yet sourced")
SOURCED = Costs(5_000, 50_000, 2_000, 3, "INR", "test fixture")


def alert(aid: int, vid: str, rule: str, mode: str = "COOLING_FAILURE", **kw: object) -> OpenAlert:
    base: dict[str, object] = {
        "alert_id": aid, "tenant_id": "t1", "vehicle_id": vid, "failure_mode": mode,
        "rule_code": rule, "severity": "critical" if rule.endswith("OVERHEAT") else "warning",
        "age_hours": 0.0,
    }  # fmt: skip
    base.update(kw)
    return OpenAlert(**base)  # type: ignore[arg-type]


def test_haversine_known_distance() -> None:
    # Chennai -> Bengaluru is ~290 km great-circle
    assert haversine_km(13.0827, 80.2707, 12.9716, 77.5946) == pytest.approx(290, abs=5)


def test_one_candidate_per_vehicle_mode_with_max_risk_and_tightest_deadline() -> None:
    cands = build_candidates(
        [alert(1, "v1", "COOLANT_DRIFT"), alert(2, "v1", "COOLANT_OVERHEAT", age_hours=4.0)],
        CAL,
        {},
    )
    assert len(cands) == 1
    c = cands[0]
    assert c.p_failure == 0.9
    assert c.source_alert_id == 2
    assert c.hours_remaining == pytest.approx(6.0)  # 10 h p10 minus 4 h already elapsed


def test_explicit_estimate_from_alert_beats_calibrated_deadline() -> None:
    a = alert(1, "v1", "TYRE_SLOW_LEAK", mode="TYRE_SLOW_LEAK", estimated_hours_to_critical=5.0)
    assert build_candidates([a], CAL, {})[0].hours_remaining == pytest.approx(5.0)


def test_unknown_rule_uses_fallback_probability() -> None:
    assert build_candidates([alert(1, "v1", "NEW_RULE")], CAL, {})[0].p_failure == 0.4


def test_placeholder_costs_never_become_money() -> None:
    c = build_candidates([alert(1, "v1", "COOLANT_OVERHEAT")], CAL,
                         {("t1", "COOLING_FAILURE"): PLACEHOLDER})[0]  # fmt: skip
    assert c.value_basis == "risk_only"
    assert c.expected_cost_avoided is None
    assert c.currency is None
    assert c.value == pytest.approx(0.9 * 1.0)


def test_sourced_costs_give_expected_cost_avoided() -> None:
    c = build_candidates([alert(1, "v1", "COOLANT_OVERHEAT")], CAL,
                         {("t1", "COOLING_FAILURE"): SOURCED})[0]  # fmt: skip
    assert c.value_basis == "cost"
    assert c.expected_cost_avoided == pytest.approx(0.9 * (50_000 - 5_000 + 2_000 * 3))
    assert c.currency == "INR"


def shops() -> list[Workshop]:
    return [
        Workshop("near", "t1", 13.00, 80.20, 1),
        Workshop("far", "t1", 13.30, 80.20, 1),
        Workshop("other-tenant", "t2", 13.00, 80.20, 99),
    ]


def test_highest_value_gets_the_nearest_earliest_slot() -> None:
    cands = build_candidates(
        [alert(1, "low", "COOLANT_DRIFT"), alert(2, "high", "COOLANT_OVERHEAT")], CAL, {}
    )
    for c in cands:
        c.location = (13.0, 80.2)
    ordered = schedule(cands, shops(), {})
    assert [c.vehicle_id for c in ordered] == ["high", "low"]
    high, low = ordered
    assert (high.workshop_id, high.day) == ("near", 0)
    # near is full today: the lower-value vehicle goes to the far workshop the same day
    assert (low.workshop_id, low.day) == ("far", 0)
    assert low.distance_km == pytest.approx(33.4, abs=0.5)


def test_capacity_already_booked_is_respected() -> None:
    cands = build_candidates([alert(1, "v1", "COOLANT_DRIFT")], CAL, {})
    cands[0].location = (13.0, 80.2)
    (c,) = schedule(cands, shops(), {("near", 0): 1, ("far", 0): 1})
    assert (c.workshop_id, c.day) == ("near", 1)


def test_no_slot_before_deadline_books_late_and_flags_it() -> None:
    cands = build_candidates([alert(1, "v1", "COOLANT_OVERHEAT")], CAL, {})  # deadline day 0
    cands[0].location = (13.0, 80.2)
    (c,) = schedule(cands, shops(), {("near", 0): 1, ("far", 0): 1})
    assert c.late is True
    assert c.day == 1
    assert "AFTER the predicted failure" in c.reasons[-1]


def test_full_horizon_leaves_candidate_unscheduled() -> None:
    cands = build_candidates([alert(1, "v1", "COOLANT_DRIFT")], CAL, {})
    booked = {(w, d): 1 for w in ("near", "far") for d in range(7)}
    (c,) = schedule(cands, shops(), booked)
    assert c.workshop_id is None
    assert c.day is None
    assert "no workshop capacity" in c.reasons[-1]


def test_never_books_another_tenants_workshop() -> None:
    a = alert(1, "v9", "COOLANT_DRIFT", tenant_id="t3")
    (c,) = schedule(build_candidates([a], CAL, {}), shops(), {})
    assert c.workshop_id is None


def test_shipped_calibration_loads_and_is_valid() -> None:
    cal = Calibration.load()
    assert cal.horizon_hours == 168
    assert cal.rules, "calibration has no rules"
    for code, rule in cal.rules.items():
        assert 0.0 <= rule.p_failure <= 1.0, code
    assert 0.0 <= cal.fallback.p_failure <= 1.0


def test_vehicle_already_past_predicted_failure_gets_today_and_is_flagged() -> None:
    cands = build_candidates([alert(1, "v1", "COOLANT_OVERHEAT", age_hours=15.0)], CAL, {})
    cands[0].location = (13.0, 80.2)
    (c,) = schedule(cands, shops(), {})
    assert c.day == 0
    assert c.late is True


def test_fresh_model_score_replaces_rule_probability_and_is_recorded() -> None:
    from prognos_stream.planner import ModelRisk

    alerts = [alert(1, "v1", "COOLANT_DRIFT"), alert(2, "v2", "COOLANT_DRIFT")]
    cands = {c.vehicle_id: c for c in build_candidates(
        alerts, CAL, {}, {"v1": ModelRisk(0.97, "failure-7d-v2")})}  # fmt: skip
    assert cands["v1"].p_failure == 0.97
    assert cands["v1"].model_version == "failure-7d-v2"
    assert cands["v1"].hours_remaining == pytest.approx(30.0)  # deadline still from the rule
    assert cands["v2"].p_failure == 0.6
    assert cands["v2"].model_version == "rules-calibrated-test"

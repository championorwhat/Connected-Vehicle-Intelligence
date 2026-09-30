"""Physics plausibility and failure-degradation ground truth."""

from __future__ import annotations

import numpy as np
import pytest

from prognos_common.roster import generate_roster
from prognos_sim.fleet import (
    COOLING,
    EVT_BREAKDOWN,
    HV_THERMAL,
    LV_BATTERY,
    MISFIRE,
    NO_FAULT,
    TYRE_LEAK,
    Fleet,
)

START = 1_790_000_000.0


@pytest.fixture(scope="module")
def vehicles():  # type: ignore[no-untyped-def]
    return generate_roster(2_000, 5, 42).vehicles


def make_fleet(vehicles, fault_rate: float = 0.0, scale: float = 1.0) -> Fleet:  # type: ignore[no-untyped-def]
    return Fleet.create(
        vehicles, seed=1, start_ts=START, fault_rate=fault_rate, fault_time_scale=scale
    )


def run(fleet: Fleet, seconds: float, dt: float = 1.0, t0: float = START) -> float:
    t = t0
    for _ in range(int(seconds / dt)):
        t += dt
        fleet.step(t, dt)
    return t


def test_physics_stays_plausible(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles)
    odometer_start = fleet.odometer.copy()
    run(fleet, 3_600)
    assert fleet.speed.min() >= 0
    assert fleet.speed.max() <= 130
    assert (fleet.odometer >= odometer_start).all()
    # Vehicles stay within their operating radius (+ margin) of home.
    dy = (fleet.lat - fleet.home_lat) * 111.32
    dx = (fleet.lon - fleet.home_lon) * 111.32 * np.cos(np.radians(fleet.lat))
    assert np.hypot(dx, dy).max() < 160
    assert fleet.soc.min() >= 0
    assert fleet.soc.max() <= 100
    # Roughly 30-60% of vehicles are driving at any moment.
    assert 0.2 < fleet.driving.mean() < 0.7


def test_evs_report_no_engine_signals(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles)
    run(fleet, 60)
    bev = np.flatnonzero(fleet.is_bev)
    sig = fleet.signals(bev, START + 60)
    assert (sig["rpm"] == 0).all()


def test_initial_fault_fraction_matches_rate(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles, fault_rate=0.05)
    fraction = (fleet.fault_mode != NO_FAULT).mean()
    assert 0.03 < fraction < 0.07
    assert len(fleet.ground_truth) == (fleet.fault_mode != NO_FAULT).sum()


def test_faults_respect_powertrain_eligibility(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles, fault_rate=0.5)
    faulty = np.flatnonzero(fleet.fault_mode != NO_FAULT)
    assert fleet.eligible[faulty, fleet.fault_mode[faulty]].all()
    bev_faults = fleet.fault_mode[faulty][fleet.is_bev[faulty]]
    assert not np.isin(bev_faults, [COOLING, MISFIRE]).any()


SIGNAL_TREND = {
    COOLING: ("coolant", +1),
    LV_BATTERY: ("lv", -1),
    TYRE_LEAK: ("tyre_min", -1),
    HV_THERMAL: ("cell_delta", +1),
}


@pytest.mark.parametrize("mode", [COOLING, LV_BATTERY, TYRE_LEAK, HV_THERMAL])
def test_degradation_moves_the_leading_signal(vehicles, mode: int) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles)
    index = int(np.flatnonzero(fleet.eligible[:, mode])[0])
    fleet.force_fault(index, mode, START, seconds_to_failure=1_000)
    # Keep the vehicle's operating state fixed so only degradation changes.
    fleet.driving[index] = True
    fleet.coolant_base[index] = 88.0

    def reading(t: float) -> float:
        samples = []
        for _ in range(30):
            sig = fleet.signals(np.array([index]), t)
            values = {
                "coolant": sig["coolant"][0],
                "lv": sig["lv"][0],
                "tyre_min": sig["tyres"][0].min(),
                "cell_delta": sig["cell_delta"][0],
            }
            samples.append(values[SIGNAL_TREND[mode][0]])
        return float(np.mean(samples))

    early, late = reading(START + 50), reading(START + 950)
    assert (late - early) * SIGNAL_TREND[mode][1] > 0, (early, late)


def test_misfire_increases_rpm_variance(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles)
    index = int(np.flatnonzero(fleet.eligible[:, MISFIRE])[0])
    fleet.force_fault(index, MISFIRE, START, seconds_to_failure=1_000)
    fleet.driving[index] = True
    fleet.speed[index] = 50.0
    idx = np.array([index])
    early = np.std([fleet.signals(idx, START + 10)["rpm"][0] for _ in range(300)])
    late = np.std([fleet.signals(idx, START + 990)["rpm"][0] for _ in range(300)])
    assert late > 2 * early


def test_failure_is_recorded_then_repaired(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles, scale=100.0)  # downtime 8-48 h => 5-29 min
    index = int(np.flatnonzero(fleet.eligible[:, TYRE_LEAK])[0])
    fleet.force_fault(index, TYRE_LEAK, START, seconds_to_failure=120)
    fleet.ground_truth.clear()

    t = run(fleet, 130)
    kinds = [(g.kind, g.vehicle_index) for g in fleet.ground_truth]
    assert ("FAILURE", index) in kinds
    assert fleet.broken[index]
    assert fleet.speed[index] == 0
    assert fleet.pending_event[index] == EVT_BREAKDOWN

    run(fleet, 1_800, t0=t)
    assert not fleet.broken[index]
    assert fleet.fault_mode[index] in (NO_FAULT, *range(5))  # repaired (a new fault may start)


def test_dtcs_appear_as_fault_progresses(vehicles) -> None:  # type: ignore[no-untyped-def]
    fleet = make_fleet(vehicles)
    index = int(np.flatnonzero(fleet.eligible[:, HV_THERMAL])[0])
    fleet.force_fault(index, HV_THERMAL, START, seconds_to_failure=1_000)
    idx = np.array([index])
    late_codes = set()
    for _ in range(50):
        sig = fleet.signals(idx, START + 980)
        late_codes.update(fleet.dtcs(idx, sig)[0])
    assert "P0A80" in late_codes


def test_sequence_numbers_keep_rising_across_restarts(vehicles) -> None:  # type: ignore[no-untyped-def]
    first = make_fleet(vehicles)
    run(first, 60)  # 60 events per vehicle at most
    restarted = Fleet.create(vehicles, seed=1, start_ts=START + 100, fault_rate=0.0)
    assert restarted.seq.min() > first.seq.max()

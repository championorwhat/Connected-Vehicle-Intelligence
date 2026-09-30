"""Detector rules: firing, hysteresis, idempotent fingerprints, snapshots, back-test harness."""

from __future__ import annotations

from typing import Any

import orjson

from prognos_stream.detector import Detector, fingerprint
from prognos_stream.evaluate import run as backtest

T0 = 1_790_000_000.0


def iso(ts: float) -> str:
    import time

    sec = int(ts)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec)) + f".{int((ts - sec) * 1000):03d}Z"


def event(seq: int, ts: float, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "vehicle_id": "veh-1", "tenant_id": "ten-1", "vin": "PG1CT1A59RC000001",
        "seq": seq, "event_ts": iso(ts), "event_type": "PERIODIC", "speed_kmh": 50.0,
        "ignition_on": True, "coolant_temp_c": 89.5, "engine_rpm": 2150,
        "lv_battery_v": 14.1, "hv_cell_delta_mv": None,
        "tyre_fl_kpa": 240.0, "tyre_fr_kpa": 241.0, "tyre_rl_kpa": 239.0, "tyre_rr_kpa": 240.5,
        "dtc_codes": [],
    }  # fmt: skip
    base.update(overrides)
    return base


def alerts(outputs) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return [orjson.loads(o.value) for o in outputs if o.topic == "alerts"]


def test_overheat_opens_once_and_clears_with_hysteresis() -> None:
    d = Detector()
    opened = alerts(d.process(event(1, T0, coolant_temp_c=115.0), 0, T0 + 1))
    assert [(a["rule_code"], a["status"], a["severity"]) for a in opened] == [
        ("COOLANT_OVERHEAT", "open", "critical")
    ]
    assert alerts(d.process(event(2, T0 + 1, coolant_temp_c=116.0), 0, T0 + 2)) == []
    # 108 is below the open threshold but above the clear threshold: still open (no flapping)
    assert alerts(d.process(event(3, T0 + 2, coolant_temp_c=108.0), 0, T0 + 3)) == []
    cleared = alerts(d.process(event(4, T0 + 3, coolant_temp_c=100.0), 0, T0 + 4))
    assert cleared[0]["status"] == "cleared"
    assert cleared[0]["fingerprint"] == opened[0]["fingerprint"]


def test_fingerprint_is_deterministic_for_replay() -> None:
    first = alerts(Detector().process(event(7, T0, coolant_temp_c=120.0), 0, T0))
    replay = alerts(Detector().process(event(7, T0, coolant_temp_c=120.0), 0, T0 + 99))
    assert (
        first[0]["fingerprint"]
        == replay[0]["fingerprint"]
        == fingerprint("veh-1", "COOLANT_OVERHEAT", 7)
    )


def test_critical_dtc_fires_immediately_warning_dtc_needs_repeats() -> None:
    d = Detector()
    critical = alerts(d.process(event(1, T0, dtc_codes=["P0300"]), 0, T0))
    assert critical[0]["rule_code"] == "DTC_P0300"
    assert critical[0]["failure_mode"] == "IGNITION_MISFIRE"
    fired = []
    for i in range(3):
        fired += alerts(d.process(event(10 + i, T0 + 10 + i, dtc_codes=["P0301"]), 0, T0))
    assert [a["rule_code"] for a in fired if a["status"] == "open"] == ["DTC_P0301"]


def test_info_dtcs_are_ignored() -> None:
    d = Detector()
    outs = []
    for i in range(10):
        outs += alerts(d.process(event(i, T0 + i, dtc_codes=["P0420"]), 0, T0))
    assert outs == []


def test_tyre_slow_leak_warning_with_time_to_critical() -> None:
    d = Detector()
    found = []
    for i in range(600):  # front-left loses 0.1 kPa per second
        e = event(i, T0 + i, tyre_fl_kpa=240.0 - 0.1 * i)
        found += [a for a in alerts(d.process(e, 0, T0 + i)) if a["rule_code"] == "TYRE_SLOW_LEAK"]
    assert found
    details = found[0]["details"]
    assert details["tyre"] == "front_left"
    assert details["leak_rate_pct_per_hour"] > 0
    assert details["estimated_hours_to_critical"] is not None


def test_breakdown_event_raises_critical() -> None:
    d = Detector()
    out = alerts(d.process(event(1, T0, event_type="BREAKDOWN", speed_kmh=0.0), 0, T0))
    assert out[0]["rule_code"] == "VEHICLE_BREAKDOWN"


def test_out_of_order_events_do_not_corrupt_trend_state() -> None:
    d = Detector()
    for i in range(100):
        d.process(event(i, T0 + i), 0, T0 + i)
    state = d.partitions[0]["veh-1"]
    last_ts = state.last_ts
    d.process(event(5, T0 + 5, coolant_temp_c=150.0), 0, T0 + 101)  # late + extreme
    assert state.last_ts == last_ts  # late event did not move the clock back


def test_state_snapshot_emitted_on_interval() -> None:
    d = Detector(state_interval_s=10.0)
    snapshots = []
    for i in range(35):
        for out in d.process(event(i, T0 + i), 0, T0 + i):
            if out.topic == "vehicle.state":
                snapshots.append(orjson.loads(out.value))
    assert len(snapshots) == 4  # t = 0, 10, 20, 30
    assert snapshots[-1]["health_score"] == 100
    assert snapshots[-1]["active_alerts"] == []


def test_partition_revocation_drops_state() -> None:
    d = Detector()
    d.process(event(1, T0), 3, T0)
    assert d.vehicles_tracked() == 1
    d.forget_partition(3)
    assert d.vehicles_tracked() == 0


def test_backtest_harness_scores_against_ground_truth() -> None:
    result = backtest(vehicles=40, hours=0.5, fault_rate=0.5, scale=480.0, seed=3)
    assert result["failures"] > 0
    assert result["recall"] is not None
    assert result["precision"] is not None
    assert result["run"]["raw_messages"] > 60_000


def test_demo_scenario_alerts_every_failure_mode_before_breakdown() -> None:
    """The 5-minute demo relies on this: each scripted vehicle is warned before it fails."""
    from prognos_common.roster import generate_roster
    from prognos_sim.config import Mode, PublisherKind, SimConfig
    from prognos_sim.engine import ShardSimulator
    from prognos_stream.evaluate import PipelinePublisher, _ts
    from prognos_stream.processor import Normalizer
    from prognos_stream.registry import VehicleRegistry

    roster = generate_roster(120, 3, 42)
    cfg = SimConfig(
        vehicle_count=120, events_per_second=120.0, tenant_count=3, mode=Mode.FAST, tick_hz=1,
        burst_multiplier=1.0, publisher=PublisherKind.NULL, metrics_port=0, fault_rate=0.0,
        scenario="demo",
    )  # fmt: skip
    clock = [T0]
    pipe = PipelinePublisher(Normalizer(VehicleRegistry.from_roster(roster)), Detector(), clock)
    sim = ShardSimulator(cfg, 0, roster.vehicles, pipe, T0)  # type: ignore[arg-type]
    for tick in range(1_150):  # the last demo failure is scheduled at +1080 s
        clock[0] = T0 + tick
        sim.tick(clock[0], 1.0)
    demo = [t for t in pipe.truth if t["type"] == "FAULT_ONSET" and t["scenario"]]
    assert len({t["vin"] for t in demo}) == 5
    for fault in demo:
        warned = [
            a for a in pipe.alerts
            if a["status"] == "open" and a["vin"] == fault["vin"]
            and a["failure_mode"] == fault["failure_mode"]
            and _ts(a["event_ts"]) < _ts(fault["failure_ts"])
        ]  # fmt: skip
        assert warned, f"no alert before {fault['failure_mode']} failure of {fault['vin']}"

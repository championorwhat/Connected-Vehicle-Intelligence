"""Contract: everything the normalizer publishes validates against the published JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import orjson
from jsonschema import Draft202012Validator, FormatChecker

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.publishers import MemoryPublisher
from prognos_stream.processor import CANONICAL, Normalizer
from prognos_stream.registry import VehicleRegistry

SCHEMA = json.loads(
    (Path(__file__).parents[2] / "packages/schemas/canonical-telemetry-v1.schema.json").read_text()
)
START = 1_790_000_000.0


def test_schema_is_valid() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_every_canonical_event_matches_schema() -> None:
    roster = generate_roster(300, 3, 42)
    cfg = SimConfig(
        vehicle_count=300, events_per_second=300.0, tenant_count=3, mode=Mode.FAST,
        publisher=PublisherKind.NULL, metrics_port=0, fault_rate=0.2,
    )  # fmt: skip
    pub = MemoryPublisher()
    sim = ShardSimulator(cfg, 0, roster.vehicles, pub, START)
    for tick in range(100):
        sim.tick(START + tick * 0.1, 0.1)
    sim.finish()

    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    normalizer = Normalizer(VehicleRegistry.from_roster(roster))
    checked = 0
    for topic, _key, value, headers in pub.records:
        if topic != "telemetry.raw":
            continue
        hdrs = {k: v for k, v in headers if isinstance(v, bytes)}
        outcome = normalizer.process(value, hdrs, 0, START + 60)
        if outcome.kind == CANONICAL:
            errors = list(validator.iter_errors(orjson.loads(outcome.value)))
            assert not errors, errors[0].message
            checked += 1
    assert checked > 2_500


def test_detector_alerts_match_alert_schema() -> None:
    from prognos_stream.detector import Detector
    from prognos_stream.evaluate import PipelinePublisher

    alert_schema = json.loads(
        (Path(__file__).parents[2] / "packages/schemas/alert-v1.schema.json").read_text()
    )
    validator = Draft202012Validator(alert_schema)
    roster = generate_roster(60, 3, 7)
    cfg = SimConfig(
        vehicle_count=60, events_per_second=60.0, tenant_count=3, mode=Mode.FAST, tick_hz=1,
        burst_multiplier=1.0, publisher=PublisherKind.NULL, metrics_port=0, fault_rate=0.6,
        fault_time_scale=960.0,
    )  # fmt: skip
    clock = [START]
    pipe = PipelinePublisher(Normalizer(VehicleRegistry.from_roster(roster)), Detector(), clock)
    sim = ShardSimulator(cfg, 0, roster.vehicles, pipe, START)
    for tick in range(1_200):
        clock[0] = START + tick
        sim.tick(clock[0], 1.0)
    sim.finish()
    assert len(pipe.alerts) > 20
    for alert in pipe.alerts:
        errors = list(validator.iter_errors(alert))
        assert not errors, errors[0].message
    assert {a["status"] for a in pipe.alerts} == {"open", "cleared"}

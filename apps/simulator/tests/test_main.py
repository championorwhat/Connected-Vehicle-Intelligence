"""End-to-end runs of the multi-process orchestrator (fast mode, no Kafka)."""

from __future__ import annotations

import json
from pathlib import Path

import orjson

from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.main import aggregate, main, run


def _config(**overrides: object) -> SimConfig:
    base: dict[str, object] = {
        "vehicle_count": 400,
        "events_per_second": 400.0,
        "tenant_count": 4,
        "sim_workers": 2,
        "mode": Mode.FAST,
        "publisher": PublisherKind.NULL,
        "duration_seconds": 5.0,
        "start_epoch": 1_790_000_000.0,
        "metrics_port": 0,
        "stats_interval_seconds": 0.5,
    }
    base.update(overrides)
    return SimConfig(**base)  # type: ignore[arg-type]


def test_two_workers_split_the_rate_exactly() -> None:
    summary = run(_config())
    assert summary["workers_completed"] == 2
    assert summary["events_generated"] == 400 * 5
    totals = summary["totals"]
    assert totals["published"] >= totals["events_generated"]  # + duplicates + ground truth


def test_demo_scenario_reports_vins() -> None:
    summary = run(_config(scenario="demo", sim_workers=1))
    assert len(summary["demo_vins"]) == 5  # one vehicle per failure mode


def test_file_publisher_writes_jsonl(tmp_path: Path) -> None:
    run(_config(publisher=PublisherKind.FILE, output_dir=str(tmp_path), sim_workers=1))
    lines = (tmp_path / "telemetry.raw-w0.jsonl").read_bytes().splitlines()
    assert len(lines) >= 2_000
    parsed = 0
    for line in lines:
        try:
            orjson.loads(line)
            parsed += 1
        except orjson.JSONDecodeError:
            pass  # malformed payloads are expected
    assert parsed / len(lines) > 0.98


def test_cli_writes_summary(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("METRICS_PORT", "0")
    monkeypatch.setenv("TENANT_COUNT", "4")
    monkeypatch.setenv("START_EPOCH", "1790000000")
    out = tmp_path / "summary.json"
    code = main(
        ["--mode", "fast", "--publisher", "null", "--vehicles", "200", "--rate", "200",
         "--workers", "1", "--duration", "3", "--summary", str(out)]
    )  # fmt: skip
    assert code == 0
    summary = json.loads(out.read_text())
    assert summary["events_generated"] == 600
    assert summary["environment"]["cpu_count"] >= 1


def test_aggregate_ignores_non_numeric() -> None:
    state = {0: {"a": 1, "demo_vins": ["X"], "flag": True}, 1: {"a": 2, "b": 3}}
    assert aggregate(state) == {"a": 3, "b": 3}

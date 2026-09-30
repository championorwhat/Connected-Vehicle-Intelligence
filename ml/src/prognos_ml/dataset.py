"""Backfill dataset: run the real pipeline offline and keep what the model needs.

    simulator -> normalizer -> detector   (in one process, simulated clock, no Kafka)

Writes one directory per run:
  events.parquet    canonical telemetry (the columns the features use)
  alerts.parquet    detector alert transitions (for the rule baseline)
  truth.parquet     ground truth: fault onsets and failures
  vehicles.parquet  static attributes (model, powertrain, firmware)
  run.json          configuration and counts

The pipeline is the production code path (normalizer + detector), so the model
learns from the same canonical events the live system produces, including the
effect of duplicates, late and malformed messages.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from prognos_common.catalog import MODEL_BY_CODE
from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_stream.detector import Detector
from prognos_stream.evaluate import START, _ts
from prognos_stream.processor import CANONICAL, Normalizer
from prognos_stream.registry import VehicleRegistry

NUMERIC = (
    "speed_kmh", "odometer_km", "engine_rpm", "coolant_temp_c", "lv_battery_v",
    "hv_soc_pct", "hv_soh_pct", "hv_pack_temp_c", "hv_cell_delta_mv",
    "tyre_fl_kpa", "tyre_fr_kpa", "tyre_rl_kpa", "tyre_rr_kpa",
)  # fmt: skip
EVENT_SCHEMA = pa.schema(
    [("vehicle_id", pa.string()), ("ts", pa.float64()), ("event_type", pa.string()),
     ("ignition_on", pa.bool_())]
    + [(name, pa.float64()) for name in NUMERIC]
    + [("dtc_codes", pa.list_(pa.string()))]
)  # fmt: skip


@dataclass(frozen=True)
class RunConfig:
    name: str
    vehicles: int = 300
    hours: float = 8.0
    fault_rate: float = 0.2
    time_scale: float = 48.0
    seed: int = 42


class _EventWriter:
    """Buffers canonical events column-wise and appends row groups to one Parquet file."""

    def __init__(self, path: Path, flush_rows: int = 200_000) -> None:
        self.writer = pq.ParquetWriter(path, EVENT_SCHEMA, compression="zstd")
        self.flush_rows = flush_rows
        self.columns: dict[str, list[Any]] = {name: [] for name in EVENT_SCHEMA.names}
        self.rows = 0

    def add(self, e: dict[str, Any]) -> None:
        cols = self.columns
        cols["vehicle_id"].append(e["vehicle_id"])
        cols["ts"].append(_ts(e["event_ts"]))
        cols["event_type"].append(e["event_type"])
        cols["ignition_on"].append(e.get("ignition_on"))
        for name in NUMERIC:
            cols[name].append(e.get(name))
        cols["dtc_codes"].append(e.get("dtc_codes") or [])
        self.rows += 1
        if len(cols["ts"]) >= self.flush_rows:
            self.flush()

    def flush(self) -> None:
        if self.columns["ts"]:
            self.writer.write_table(pa.table(self.columns, schema=EVENT_SCHEMA))
            self.columns = {name: [] for name in EVENT_SCHEMA.names}

    def close(self) -> None:
        self.flush()
        self.writer.close()


class _Capture:
    """Publisher: raw -> normalizer -> (events.parquet, detector -> alerts)."""

    def __init__(self, normalizer: Normalizer, detector: Detector, events: _EventWriter) -> None:
        self.normalizer = normalizer
        self.detector = detector
        self.events = events
        self.now = START
        self.alerts: list[dict[str, Any]] = []
        self.truth: list[dict[str, Any]] = []
        self.raw = 0

    def publish(self, topic: str, key: bytes, value: bytes, headers: list[Any]) -> None:
        if topic == "sim.truth":
            self.truth.append(orjson.loads(value))
            return
        self.raw += 1
        hdrs = {k: v for k, v in headers if isinstance(v, bytes)}
        outcome = self.normalizer.process(value, hdrs, 0, self.now)
        if outcome.kind != CANONICAL:
            return
        event = orjson.loads(outcome.value)
        self.events.add(event)
        for out in self.detector.process(event, 0, self.now):
            if out.topic == "alerts":
                self.alerts.append(orjson.loads(out.value))

    def poll(self) -> None:
        return None

    def flush(self, timeout: float = 30.0) -> int:
        return 0

    def stats(self) -> dict[str, int]:
        return {"published": self.raw}


def generate(cfg: RunConfig, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    roster = generate_roster(cfg.vehicles, max(1, min(20, cfg.vehicles // 20)), cfg.seed)
    sim_cfg = SimConfig(
        vehicle_count=cfg.vehicles, events_per_second=float(cfg.vehicles),
        tenant_count=len(roster.tenants), seed=cfg.seed, tick_hz=1, mode=Mode.FAST,
        publisher=PublisherKind.NULL, metrics_port=0, fault_rate=cfg.fault_rate,
        fault_time_scale=cfg.time_scale, burst_multiplier=1.0,
    )  # fmt: skip
    writer = _EventWriter(out / "events.parquet")
    capture = _Capture(Normalizer(VehicleRegistry.from_roster(roster)), Detector(), writer)
    sim = ShardSimulator(sim_cfg, 0, roster.vehicles, capture, START)
    wall = time.perf_counter()
    for tick in range(int(cfg.hours * 3600)):
        capture.now = START + tick
        sim.tick(capture.now, 1.0)
    sim.finish()
    writer.close()
    end = capture.now

    pq.write_table(
        pa.table({
            "vehicle_id": [a["vehicle_id"] for a in capture.alerts],
            "status": [a["status"] for a in capture.alerts],
            "rule_code": [a["rule_code"] for a in capture.alerts],
            "severity": [a["severity"] for a in capture.alerts],
            "failure_mode": [a["failure_mode"] for a in capture.alerts],
            "fingerprint": [a["fingerprint"] for a in capture.alerts],
            "ts": [_ts(a["event_ts"]) for a in capture.alerts],
        }),
        out / "alerts.parquet",
    )  # fmt: skip
    pq.write_table(
        pa.table({
            "vehicle_id": [t["vehicle_id"] for t in capture.truth],
            "type": [t["type"] for t in capture.truth],
            "failure_mode": [t["failure_mode"] for t in capture.truth],
            "onset_ts": [_ts(t["onset_ts"]) for t in capture.truth],
            "failure_ts": [_ts(t["failure_ts"]) for t in capture.truth],
        }),
        out / "truth.parquet",
    )  # fmt: skip
    pq.write_table(
        pa.table({
            "vehicle_id": [str(v.vehicle_id) for v in roster.vehicles],
            "model_code": [v.model_code for v in roster.vehicles],
            "powertrain": [MODEL_BY_CODE[v.model_code].powertrain.value for v in roster.vehicles],
            "firmware_version": [v.firmware_version for v in roster.vehicles],
            "model_year": [v.model_year for v in roster.vehicles],
        }),
        out / "vehicles.parquet",
    )  # fmt: skip
    info = {
        "config": asdict(cfg),
        "start_ts": START,
        "end_ts": end,
        "raw_messages": capture.raw,
        "canonical_events": writer.rows,
        "alerts": len(capture.alerts),
        "failures": sum(1 for t in capture.truth if t["type"] == "FAILURE"),
        "wall_seconds": round(time.perf_counter() - wall, 1),
    }
    (out / "run.json").write_text(json.dumps(info, indent=2) + "\n")
    return info

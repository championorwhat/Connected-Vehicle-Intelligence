"""Simulator configuration, read from environment variables (12-factor)."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from enum import StrEnum


class Mode(StrEnum):
    LIVE = "live"  # paced to wall-clock time (demo, load tests)
    FAST = "fast"  # as fast as possible on a simulated clock (benchmarks, backfill)


class PublisherKind(StrEnum):
    KAFKA = "kafka"
    NULL = "null"  # serialise and discard: measures generation cost alone
    FILE = "file"  # JSON lines per topic and worker


@dataclass(frozen=True, slots=True)
class SimConfig:
    vehicle_count: int = 10_000
    events_per_second: float = 10_000.0
    sim_workers: int = 1
    tick_hz: int = 10
    tenant_count: int = 20
    seed: int = 42

    mode: Mode = Mode.LIVE
    duration_seconds: float = 0.0  # 0 = run until stopped
    start_epoch: float = 0.0  # FAST mode start time; 0 = now

    burst_multiplier: float = 3.0
    burst_start_seconds: float = -1.0  # seconds after start; negative = no burst
    burst_duration_seconds: float = 300.0

    duplicate_rate: float = 0.01
    out_of_order_rate: float = 0.02
    max_delay_seconds: float = 30.0
    malformed_rate: float = 0.005
    missing_field_rate: float = 0.01
    network_delay_ms_mean: float = 300.0

    fault_rate: float = 0.01  # fraction of eligible vehicles degrading at any time
    fault_time_scale: float = 1.0  # >1 compresses degradation timelines (demo/backfill)
    scenario: str = "none"  # "demo": a few vehicles fail within minutes

    publisher: PublisherKind = PublisherKind.KAFKA
    kafka_bootstrap_servers: str = "localhost:9092"
    raw_topic: str = "telemetry.raw"
    ground_truth_topic: str = "sim.truth"
    output_dir: str = "data/sim-output"
    metrics_port: int = 9101  # 0 disables the Prometheus endpoint
    stats_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.vehicle_count < self.tenant_count:
            raise ValueError("vehicle_count must be >= tenant_count")
        if self.events_per_second <= 0 or self.tick_hz <= 0 or self.sim_workers <= 0:
            raise ValueError("events_per_second, tick_hz and sim_workers must be positive")
        peak_per_tick = self.events_per_second * max(self.burst_multiplier, 1.0) / self.tick_hz
        if peak_per_tick > self.vehicle_count:
            raise ValueError("peak events per tick exceeds vehicle count; raise TICK_HZ")
        for name in ("duplicate_rate", "out_of_order_rate", "malformed_rate",
                     "missing_field_rate", "fault_rate"):  # fmt: skip
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.fault_time_scale <= 0 or self.burst_multiplier <= 0:
            raise ValueError("fault_time_scale and burst_multiplier must be positive")

    def rate_multiplier(self, elapsed: float) -> float:
        """Burst schedule: BURST_MULTIPLIER x during [start, start + duration)."""
        start = self.burst_start_seconds
        if start >= 0 and start <= elapsed < start + self.burst_duration_seconds:
            return self.burst_multiplier
        return 1.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **overrides: object) -> SimConfig:
        source = os.environ if env is None else env
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = source.get(f.name.upper())
            if raw is None or raw == "":
                continue
            default = f.default
            if isinstance(default, bool):
                values[f.name] = raw.lower() in {"1", "true", "yes"}
            elif isinstance(default, StrEnum):
                values[f.name] = type(default)(raw.lower())
            elif isinstance(default, int):
                values[f.name] = int(raw)
            elif isinstance(default, float):
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        # Compose uses KAFKA_BOOTSTRAP_SERVERS for every service.
        # (Only when set: on a slots dataclass, cls.<field> is a descriptor, not the default.)
        if source.get("KAFKA_BOOTSTRAP_SERVERS"):
            values.setdefault("kafka_bootstrap_servers", source["KAFKA_BOOTSTRAP_SERVERS"])
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]

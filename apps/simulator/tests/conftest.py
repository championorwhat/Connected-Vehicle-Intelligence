from __future__ import annotations

from collections.abc import Callable

import pytest

from prognos_common.roster import generate_roster
from prognos_sim.config import Mode, PublisherKind, SimConfig
from prognos_sim.engine import ShardSimulator
from prognos_sim.publishers import MemoryPublisher

START = 1_790_000_000.0  # fixed simulated epoch => deterministic runs


def make_config(**overrides: object) -> SimConfig:
    base: dict[str, object] = {
        "vehicle_count": 1_000,
        "events_per_second": 1_000.0,
        "tenant_count": 5,
        "mode": Mode.FAST,
        "publisher": PublisherKind.NULL,
        "metrics_port": 0,
    }
    base.update(overrides)
    return SimConfig(**base)  # type: ignore[arg-type]


RunFn = Callable[..., tuple[ShardSimulator, MemoryPublisher]]


@pytest.fixture
def run_sim() -> RunFn:
    """Run one shard for `seconds` of simulated time and return it with its captured output."""

    def _run(seconds: float = 10.0, **overrides: object) -> tuple[ShardSimulator, MemoryPublisher]:
        cfg = make_config(**overrides)
        roster = generate_roster(cfg.vehicle_count, cfg.tenant_count, cfg.seed)
        publisher = MemoryPublisher()
        sim = ShardSimulator(cfg, 0, roster.vehicles, publisher, START)
        dt = 1.0 / cfg.tick_hz
        for tick in range(int(seconds * cfg.tick_hz)):
            elapsed = tick * dt
            sim.tick(START + elapsed, dt, cfg.rate_multiplier(elapsed))
        sim.finish()
        return sim, publisher

    return _run

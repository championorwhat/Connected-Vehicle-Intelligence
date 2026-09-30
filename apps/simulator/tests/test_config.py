import pytest

from prognos_sim.config import Mode, PublisherKind, SimConfig


def test_from_env_parses_types() -> None:
    cfg = SimConfig.from_env(
        {
            "VEHICLE_COUNT": "100000",
            "EVENTS_PER_SECOND": "100000",
            "SIM_WORKERS": "4",
            "DUPLICATE_RATE": "0.02",
            "MODE": "FAST",
            "PUBLISHER": "null",
            "KAFKA_BOOTSTRAP_SERVERS": "kafka:19092",
        }
    )
    assert cfg.vehicle_count == 100_000
    assert cfg.sim_workers == 4
    assert cfg.duplicate_rate == 0.02
    assert cfg.mode is Mode.FAST
    assert cfg.publisher is PublisherKind.NULL
    assert cfg.kafka_bootstrap_servers == "kafka:19092"


def test_burst_schedule() -> None:
    cfg = SimConfig(burst_start_seconds=60, burst_duration_seconds=300, burst_multiplier=3)
    assert cfg.rate_multiplier(59.9) == 1.0
    assert cfg.rate_multiplier(60) == 3.0
    assert cfg.rate_multiplier(359.9) == 3.0
    assert cfg.rate_multiplier(360) == 1.0
    assert SimConfig().rate_multiplier(100) == 1.0  # no burst configured by default


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duplicate_rate": 1.5},
        {"vehicle_count": 5, "tenant_count": 20},
        {"events_per_second": 0},
        {"vehicle_count": 1000, "events_per_second": 50_000, "tick_hz": 10},
        {"fault_time_scale": 0},
    ],
)
def test_invalid_config_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SimConfig(**kwargs)  # type: ignore[arg-type]


def test_from_env_without_bootstrap_keeps_default() -> None:
    """Regression: on a slots dataclass, cls.<field> is a descriptor, not the default."""
    cfg = SimConfig.from_env({})
    assert cfg.kafka_bootstrap_servers == "localhost:9092"

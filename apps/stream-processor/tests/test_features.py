import math
import random

import pytest

from prognos_stream.features import Cusum, Ewma, EwTrend, EwVar


def test_ewma_converges_and_first_value_seeds() -> None:
    e = Ewma(0.1)
    assert e.update(10.0) == 10.0
    for _ in range(200):
        e.update(20.0)
    assert e.value == pytest.approx(20.0, abs=1e-6)


def test_ewvar_estimates_noise_std() -> None:
    rng = random.Random(1)
    v = EwVar(0.01)
    for _ in range(20_000):
        v.update(rng.gauss(5.0, 2.0))
    assert v.mean == pytest.approx(5.0, abs=0.3)
    assert v.std == pytest.approx(2.0, rel=0.15)


def test_cusum_ignores_noise_below_k_and_detects_shift() -> None:
    rng = random.Random(2)
    c = Cusum(k=4.0)
    for _ in range(5_000):
        c.update(rng.gauss(0.0, 0.6))
    assert c.s < 5.0  # in-control noise stays near zero
    samples = 0
    while c.update(rng.gauss(8.0, 0.6)) <= 60.0:
        samples += 1
    assert samples < 20  # ~60 / (8 - 4) = 15 samples to alarm


def test_trend_recovers_known_slope() -> None:
    t = EwTrend(tau_s=3600.0)
    for second in range(0, 7200, 10):
        t.update(1_790_000_000.0 + second, 0.05 * second / 3600.0)  # +0.05 per hour
    assert t.slope_per_hour == pytest.approx(0.05, rel=1e-6)


def test_trend_needs_three_points() -> None:
    t = EwTrend(600.0)
    t.update(0.0, 1.0)
    t.update(1.0, 2.0)
    assert t.slope_per_hour == 0.0
    t.update(2.0, 3.0)
    assert math.isfinite(t.slope_per_hour)
    assert t.slope_per_hour > 0

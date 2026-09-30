"""O(1)-per-sample streaming statistics used by the detector.

Each keeps a handful of floats per vehicle and signal, so 100K vehicles need
a few tens of MB of state regardless of history length.

- Ewma            exponentially weighted mean                      O(1) time/space
- EwVar           exponentially weighted mean + variance (West)     O(1)
- Cusum           one-sided CUSUM change detector (Page, 1954)       O(1)
- EwTrend         exponentially time-weighted least-squares slope    O(1)
"""

from __future__ import annotations

import math


class Ewma:
    __slots__ = ("alpha", "n", "value")

    def __init__(self, alpha: float) -> None:
        self.alpha = alpha
        self.value = 0.0
        self.n = 0

    def update(self, x: float) -> float:
        self.value = x if self.n == 0 else self.value + self.alpha * (x - self.value)
        self.n += 1
        return self.value


class EwVar:
    """Exponentially weighted mean and variance (West, 1979 incremental form)."""

    __slots__ = ("alpha", "mean", "n", "var")

    def __init__(self, alpha: float) -> None:
        self.alpha = alpha
        self.mean = 0.0
        self.var = 0.0
        self.n = 0

    def update(self, x: float) -> None:
        if self.n == 0:
            self.mean = x
        else:
            diff = x - self.mean
            incr = self.alpha * diff
            self.mean += incr
            self.var = (1.0 - self.alpha) * (self.var + diff * incr)
        self.n += 1

    @property
    def std(self) -> float:
        return math.sqrt(self.var)


class Cusum:
    """Upper CUSUM: s = max(0, s + x - k). Alarms when s > h.

    Detects a sustained upward mean shift larger than k with an average delay
    of roughly h / (shift - k) samples, while in-control noise below k decays s to 0.
    """

    __slots__ = ("k", "s")

    def __init__(self, k: float) -> None:
        self.k = k
        self.s = 0.0

    def update(self, x: float) -> float:
        self.s = max(0.0, self.s + x - self.k)
        return self.s


class EwTrend:
    """Exponentially time-weighted least-squares slope of y against time.

    Weights decay as exp(-dt / tau) between samples, so the slope reflects roughly
    the last `tau` seconds. Returns the slope in y-units per hour.
    Time is kept relative to the first sample to avoid float cancellation.
    """

    __slots__ = ("n", "sw", "swt", "swtt", "swty", "swy", "t0", "t_last", "tau")

    def __init__(self, tau_s: float) -> None:
        self.tau = tau_s
        self.t0: float | None = None
        self.t_last = 0.0
        self.n = 0  # sample count (the decayed weight sum is not a count)
        self.sw = self.swt = self.swy = self.swtt = self.swty = 0.0

    def update(self, t: float, y: float) -> None:
        if self.t0 is None:
            self.t0 = t
        rel = (t - self.t0) / 3600.0
        if self.sw:
            decay = math.exp(-max(t - self.t_last, 0.0) / self.tau)
            self.sw *= decay
            self.swt *= decay
            self.swy *= decay
            self.swtt *= decay
            self.swty *= decay
        self.t_last = t
        self.n += 1
        self.sw += 1.0
        self.swt += rel
        self.swy += y
        self.swtt += rel * rel
        self.swty += rel * y

    @property
    def slope_per_hour(self) -> float:
        denominator = self.sw * self.swtt - self.swt * self.swt
        if self.n < 3 or denominator <= 1e-12:
            return 0.0
        return (self.sw * self.swty - self.swt * self.swy) / denominator

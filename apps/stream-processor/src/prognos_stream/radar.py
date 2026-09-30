"""Emerging-fault radar: a DTC that is suddenly common in one cohort of the fleet.

A single vehicle's DTC is the detector's job. The radar looks across vehicles for
a *systematic* problem - a bad firmware release, a bad parts batch - which no
single vehicle can reveal.

Cohorts (two levels, each compared with the right baseline):
  firmware  (model_code, firmware_version)  vs  same model, other firmware versions
  model     model_code                      vs  other models with the same powertrain
Comparing like with like matters: HV codes only exist on EVs, so an EV model
compared with the whole (mostly combustion) fleet would always look anomalous.

Per tumbling event-time window (default 1 h), for each cohort c and DTC d:
  k = vehicles in c that reported d          n = vehicles registered in c
  K, N = the same for the baseline group
  p0 = (K + 0.5) / (N + 1)                   (smoothed: a zero baseline stays testable)
  p  = P(X >= k), X ~ Poisson(n * p0)        (exact tail; k is small, p0 is tiny)
A signal fires when k >= min_vehicles, k/n >= min_ratio * p0 and the
Bonferroni-adjusted p (p * number of pairs tested in the window) < alpha.

Why not a Count-Min Sketch: the key space is ~9 models x 2 firmwares x ~20 DTCs,
so exact sets fit in a few MB; a sketch would add error for no memory benefit.
Distinct vehicles (not event counts) are counted so one chatty vehicle cannot
fake a fleet-wide pattern.

Window close is driven by the watermark (max event time seen - allowed lateness);
events older than a closed window are counted as `late_dropped`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

FIRMWARE, MODEL = "firmware", "model"


@dataclass(frozen=True, slots=True)
class Cohort:
    oem: str
    model_code: str
    firmware: str
    powertrain: str


@dataclass
class RadarConfig:
    window_s: float = 3600.0
    allowed_lateness_s: float = 120.0
    min_vehicles: int = 5
    min_ratio: float = 3.0
    alpha: float = 0.001


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), summed in log space (stable for large lam)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    # P(X >= k) = 1 - sum_{i<k} e^-lam lam^i / i!; sum the tail directly when it is tiny
    log_term = -lam  # i = 0
    head = 0.0
    for i in range(k):
        if i:
            log_term += math.log(lam) - math.log(i)
        head += math.exp(log_term)
    tail = 1.0 - head
    if tail > 1e-9:
        return tail
    # Direct tail sum avoids cancellation when the head is ~1.
    log_term += math.log(lam) - math.log(k)  # i = k
    total, i = 0.0, k
    while True:
        term = math.exp(log_term)
        total += term
        if term < total * 1e-15 or i > k + 10_000:
            return total
        i += 1
        log_term += math.log(lam) - math.log(i)


@dataclass
class _Window:
    start: float
    # dtc -> cohort -> vehicles
    seen: dict[str, dict[Cohort, set[str]]] = field(default_factory=dict)


class Radar:
    def __init__(self, cohorts: dict[str, Cohort], cfg: RadarConfig | None = None) -> None:
        self.cfg = cfg or RadarConfig()
        self.windows: dict[float, _Window] = {}
        self.watermark = -math.inf
        self.closed_before = -math.inf
        self.counts: dict[str, int] = dict.fromkeys(
            ("events", "dtc_events", "late_dropped", "unknown_vehicle", "windows_closed",
             "signals"), 0
        )  # fmt: skip
        self.set_cohorts(cohorts)

    def set_cohorts(self, cohorts: dict[str, Cohort]) -> None:
        """(Re)load the vehicle -> cohort map; newly onboarded vehicles join the population."""
        self.cohort_of = cohorts
        self.population: dict[Cohort, int] = {}
        self.model_pop: dict[str, int] = {}
        self.model_meta: dict[str, tuple[str, str]] = {}  # model -> (oem, powertrain)
        self.pt_pop: dict[str, int] = {}
        for c in cohorts.values():
            self.population[c] = self.population.get(c, 0) + 1
            self.model_pop[c.model_code] = self.model_pop.get(c.model_code, 0) + 1
            self.model_meta[c.model_code] = (c.oem, c.powertrain)
            self.pt_pop[c.powertrain] = self.pt_pop.get(c.powertrain, 0) + 1

    def add(self, vehicle_id: str, ts: float, dtcs: list[str]) -> list[dict[str, Any]]:
        """Record one event; returns signals from any windows this event closes."""
        self.counts["events"] += 1
        if ts > self.watermark:
            self.watermark = ts
        if dtcs:
            self.counts["dtc_events"] += 1
            start = ts - ts % self.cfg.window_s
            cohort = self.cohort_of.get(vehicle_id)
            if cohort is None:
                self.counts["unknown_vehicle"] += 1
            elif start < self.closed_before:
                self.counts["late_dropped"] += 1
            else:
                w = self.windows.get(start)
                if w is None:
                    w = self.windows[start] = _Window(start)
                for code in dtcs:
                    w.seen.setdefault(code, {}).setdefault(cohort, set()).add(vehicle_id)
        return self.close_ready()

    def advance(self, ts: float) -> list[dict[str, Any]]:
        """Move the watermark without an event (e.g. a clean event with no DTC)."""
        if ts > self.watermark:
            self.watermark = ts
        return self.close_ready()

    def close_ready(self) -> list[dict[str, Any]]:
        horizon = self.watermark - self.cfg.allowed_lateness_s
        # A window [s, s+W) is complete once the watermark passes its end + lateness.
        limit = horizon - horizon % self.cfg.window_s
        if limit <= self.closed_before:
            return []
        signals: list[dict[str, Any]] = []
        for start in sorted(s for s in self.windows if s + self.cfg.window_s <= horizon):
            signals.extend(self._evaluate(self.windows.pop(start)))
            self.counts["windows_closed"] += 1
        self.closed_before = max(self.closed_before, limit)
        self.counts["signals"] += len(signals)
        return signals

    # ------------------------------------------------------------------ statistics
    def _evaluate(self, w: _Window) -> list[dict[str, Any]]:
        # (level, dtc, cohort key, k, n, K, N)
        tests: list[tuple[str, str, Cohort | str, int, int, int, int]] = []
        for code, by_cohort in w.seen.items():
            model_k: dict[str, int] = {}
            for c, vehicles in by_cohort.items():
                model_k[c.model_code] = model_k.get(c.model_code, 0) + len(vehicles)
            pt_k: dict[str, int] = {}
            for model, k in model_k.items():
                pt = self.model_meta[model][1]
                pt_k[pt] = pt_k.get(pt, 0) + k
            for c, vehicles in by_cohort.items():
                k, n = len(vehicles), self.population[c]
                N = self.model_pop[c.model_code] - n
                if N > 0:  # a model with a single firmware has no firmware-level baseline
                    tests.append((FIRMWARE, code, c, k, n, model_k[c.model_code] - k, N))
            for model, k in model_k.items():
                pt = self.model_meta[model][1]
                n = self.model_pop[model]
                N = self.pt_pop[pt] - n
                if N > 0:
                    tests.append((MODEL, code, model, k, n, pt_k[pt] - k, N))

        n_tests = len(tests)
        out: list[dict[str, Any]] = []
        for level, code, cohort, k, n, K, N in tests:
            if k < self.cfg.min_vehicles or n == 0:
                continue
            p0 = (K + 0.5) / (N + 1.0)
            rate = k / n
            if rate < self.cfg.min_ratio * p0:
                continue
            p_value = poisson_sf(k, n * p0)
            p_adj = min(1.0, p_value * n_tests)
            if p_adj >= self.cfg.alpha:
                continue
            ident: dict[str, str | None]
            if isinstance(cohort, Cohort):
                ident = {
                    "oem": cohort.oem, "model_code": cohort.model_code,
                    "firmware_version": cohort.firmware, "powertrain": cohort.powertrain,
                }  # fmt: skip
                baseline = "same model, other firmware versions"
            else:
                oem, pt = self.model_meta[cohort]
                ident = {"oem": oem, "model_code": cohort, "firmware_version": None,
                         "powertrain": pt}  # fmt: skip
                baseline = f"other {pt} models"
            out.append({
                "schema_version": 1,
                "signal_type": "EMERGING_DTC",
                "level": level,
                "dtc": code,
                **ident,
                "window_start": w.start,
                "window_end": w.start + self.cfg.window_s,
                "affected_vehicles": k,
                "cohort_vehicles": n,
                "rate": round(rate, 5),
                "baseline": baseline,
                "baseline_affected": K,
                "baseline_vehicles": N,
                "baseline_rate": round(K / N, 5),
                "rate_ratio": round(rate / p0, 2),
                "p_value": p_value,
                "p_adjusted": p_adj,
                "tests_in_window": n_tests,
            })  # fmt: skip
        out.sort(key=lambda s: (s["p_adjusted"], s["dtc"], s["level"]))
        return out

"""Maintenance planner: open alerts -> calibrated risk -> value -> workshop slots.

Pure logic, no I/O (the service in planner_main.py does the database work).

1. Risk.   Each open alert's rule has a calibrated probability that the vehicle
           fails within 7 days (calibration/rules-v1.json, measured by the
           back-test with a Wilson 95% interval). Several alerts on the same
           vehicle and failure mode are correlated evidence, so the vehicle's risk
           is the max, not a noisy-OR (which would assume independence).
2. Value.  With sourced repair costs:
               value = p * (unplanned - planned + downtime_per_day * extra_days)
           Costs marked PLACEHOLDER (or all zero) are never turned into money:
               value = p * severity_weight   and expected_cost_avoided = NULL
3. Slots.  Greedy by value (highest first). Each candidate gets the earliest day,
           then the nearest of its tenant's K nearest workshops, that still has
           capacity on that day, no later than its deadline (predicted hours to
           failure). If no slot fits before the deadline, it gets the earliest slot
           in the horizon and is flagged late; if the horizon is full it stays
           unscheduled. O(n log n + n*K*D) for n candidates, K workshops, D days.

Greedy is not optimal in general (it is a weighted interval/assignment problem),
but it is predictable, explainable to a fleet manager ("most valuable first"),
and it never gives a slot to a lower-value vehicle while a higher-value vehicle
that could use it is waiting.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

SEVERITY_WEIGHT = {"critical": 1.0, "warning": 0.5, "info": 0.1}
PLACEHOLDER_PREFIX = "PLACEHOLDER"
EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- calibration
@dataclass(frozen=True)
class RuleCalibration:
    p_failure: float
    hours_to_failure_p10: float | None  # conservative deadline when the alert has no estimate


@dataclass(frozen=True)
class Calibration:
    version: str
    horizon_hours: float
    rules: dict[str, RuleCalibration]
    fallback: RuleCalibration  # rules the back-test never saw

    @classmethod
    def load(cls, name: str = "rules-v1.json") -> Calibration:
        text = resources.files("prognos_stream").joinpath("calibration", name).read_text()
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Calibration:
        rules = {
            code: RuleCalibration(r["p_failure"], r.get("hours_to_failure_p10"))
            for code, r in data["rules"].items()
        }
        fb = data["fallback"]
        return cls(
            version=data["version"],
            horizon_hours=float(data["horizon_hours"]),
            rules=rules,
            fallback=RuleCalibration(fb["p_failure"], fb.get("hours_to_failure_p10")),
        )

    def rule(self, code: str) -> RuleCalibration:
        return self.rules.get(code, self.fallback)


# --------------------------------------------------------------------------- inputs
@dataclass(frozen=True)
class OpenAlert:
    alert_id: int
    tenant_id: str
    vehicle_id: str
    failure_mode: str
    rule_code: str
    severity: str
    age_hours: float  # now - event_ts
    estimated_hours_to_critical: float | None = None  # from the alert details, if any


@dataclass(frozen=True)
class Costs:
    planned: float
    unplanned: float
    downtime_per_day: float
    extra_downtime_days: float
    currency: str
    source: str

    @property
    def sourced(self) -> bool:
        return (
            not self.source.upper().startswith(PLACEHOLDER_PREFIX)
            and self.unplanned + self.downtime_per_day > 0
        )

    def avoided(self) -> float:
        return self.unplanned - self.planned + self.downtime_per_day * self.extra_downtime_days


@dataclass(frozen=True)
class Workshop:
    workshop_id: str
    tenant_id: str
    latitude: float
    longitude: float
    daily_capacity: int


# --------------------------------------------------------------------------- outputs
@dataclass
class Candidate:
    tenant_id: str
    vehicle_id: str
    failure_mode: str
    source_alert_id: int
    rule_code: str
    severity: str
    p_failure: float
    hours_remaining: float | None  # to predicted failure; None = unknown
    value: float
    value_basis: str  # "cost" or "risk_only"
    expected_cost_avoided: float | None
    currency: str | None
    location: tuple[float, float] | None = None
    # filled by the scheduler
    workshop_id: str | None = None
    day: int | None = None
    distance_km: float | None = None
    late: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def deadline_day(self) -> int | None:
        if self.hours_remaining is None:
            return None
        return max(0, math.floor(self.hours_remaining / 24.0))


def build_candidates(
    alerts: list[OpenAlert],
    calibration: Calibration,
    costs: dict[tuple[str, str], Costs],
) -> list[Candidate]:
    """One candidate per (vehicle, failure mode): the alert with the highest calibrated risk."""
    best: dict[tuple[str, str], tuple[float, OpenAlert]] = {}
    deadline: dict[tuple[str, str], float] = {}
    for a in alerts:
        key = (a.vehicle_id, a.failure_mode)
        cal = calibration.rule(a.rule_code)
        if key not in best or cal.p_failure > best[key][0]:
            best[key] = (cal.p_failure, a)
        # The tightest deadline any alert predicts wins: an explicit estimate from the
        # alert (e.g. tyre leak rate) beats the rule's calibrated 10th percentile.
        hours = a.estimated_hours_to_critical
        if hours is None and cal.hours_to_failure_p10 is not None:
            hours = cal.hours_to_failure_p10
        if hours is not None:
            remaining = hours - a.age_hours
            deadline[key] = min(deadline.get(key, math.inf), remaining)

    out: list[Candidate] = []
    for key, (p, a) in best.items():
        c = costs.get((a.tenant_id, a.failure_mode))
        reasons = [f"{a.rule_code} ({a.severity}) -> P(fail in 7 d) = {p:.2f}"]
        if c is not None and c.sourced:
            avoided = round(p * c.avoided(), 2)
            value, basis, currency = avoided, "cost", c.currency
            reasons.append(f"expected cost avoided {avoided:,.2f} {c.currency}")
        else:
            avoided, currency = None, None
            value, basis = p * SEVERITY_WEIGHT.get(a.severity, 0.1), "risk_only"
            reasons.append("repair costs not sourced: ranked by risk x severity")
        out.append(
            Candidate(
                tenant_id=a.tenant_id, vehicle_id=a.vehicle_id, failure_mode=a.failure_mode,
                source_alert_id=a.alert_id, rule_code=a.rule_code, severity=a.severity,
                p_failure=p, hours_remaining=deadline.get(key), value=value, value_basis=basis,
                expected_cost_avoided=avoided, currency=currency, reasons=reasons,
            )
        )  # fmt: skip
    return out


def schedule(
    candidates: list[Candidate],
    workshops: list[Workshop],
    booked: dict[tuple[str, int], int],
    *,
    horizon_days: int = 7,
    nearest_k: int = 3,
) -> list[Candidate]:
    """Assign workshop-days greedily by value. `booked[(workshop_id, day)]` = slots already taken.

    Returns the candidates in priority order (value desc, then tighter deadline).
    """
    by_tenant: dict[str, list[Workshop]] = {}
    for w in workshops:
        by_tenant.setdefault(w.tenant_id, []).append(w)
    used = dict(booked)

    def priority(c: Candidate) -> tuple[float, float, str]:
        hours = c.hours_remaining if c.hours_remaining is not None else math.inf
        return (-c.value, hours, c.vehicle_id)  # vehicle_id: deterministic tie-break

    ordered = sorted(candidates, key=priority)
    for c in ordered:
        shops = by_tenant.get(c.tenant_id, [])
        if not shops:
            c.reasons.append("tenant has no workshop")
            continue
        if c.location is not None:
            lat, lon = c.location
            ranked = sorted(
                ((haversine_km(lat, lon, w.latitude, w.longitude), w) for w in shops),
                key=lambda t: (t[0], t[1].workshop_id),
            )[:nearest_k]
        else:
            ranked = [(math.nan, w) for w in sorted(shops, key=lambda w: w.workshop_id)[:nearest_k]]

        deadline = c.deadline_day
        last_ok = horizon_days - 1 if deadline is None else min(horizon_days - 1, deadline)
        slot = _first_free(ranked, used, 0, last_ok)
        if slot is None and deadline is not None and deadline < horizon_days - 1:
            slot = _first_free(ranked, used, deadline + 1, horizon_days - 1)
        if slot is None:
            c.reasons.append(f"no workshop capacity in the next {horizon_days} days")
            continue
        day, dist, w = slot
        used[(w.workshop_id, day)] = used.get((w.workshop_id, day), 0) + 1
        c.workshop_id, c.day = w.workshop_id, day
        # Late = the slot starts after the predicted failure (includes vehicles already
        # past it, which get today's slot but still need escalating).
        c.late = c.hours_remaining is not None and day * 24.0 > c.hours_remaining
        c.distance_km = None if math.isnan(dist) else round(dist, 1)
        c.reasons.append(
            f"day +{day} at workshop {w.workshop_id[:8]}"
            + ("" if c.distance_km is None else f" ({c.distance_km} km)")
            + (" - AFTER the predicted failure: escalate" if c.late else "")
        )
    return ordered


def _first_free(
    ranked: list[tuple[float, Workshop]], used: dict[tuple[str, int], int], first: int, last: int
) -> tuple[int, float, Workshop] | None:
    for day in range(first, last + 1):
        for dist, w in ranked:  # nearest first
            if used.get((w.workshop_id, day), 0) < w.daily_capacity:
                return day, dist, w
    return None

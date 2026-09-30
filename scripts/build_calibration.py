"""Build the planner's rule calibration from a back-test result (with provenance).

    uv run python scripts/build_calibration.py \
        evidence/benchmarks/m7-calibration-fit-seed42.json \
        apps/stream-processor/src/prognos_stream/calibration/rules-v1.json

Rules with fewer than MIN_ALERTS eligible alerts are not trusted on their own: they
get the pooled rate of all rules (their own numbers are kept for reference).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MIN_ALERTS = 10


def build(backtest: dict, source: str, version: str) -> dict:  # type: ignore[type-arg]
    cal = backtest["calibration"]
    rules = cal["rules"]
    eligible = sum(r["eligible_alerts"] for r in rules.values())
    failed = sum(r["followed_by_failure"] for r in rules.values())
    pooled = round(failed / eligible, 3) if eligible else 0.5
    out_rules = {}
    for code, r in rules.items():
        trusted = r["eligible_alerts"] >= MIN_ALERTS
        out_rules[code] = {
            "p_failure": r["p_failure"] if trusted else pooled,
            "hours_to_failure_p10": r.get("hours_to_failure_p10"),
            "eligible_alerts": r["eligible_alerts"],
            "measured_p_failure": r["p_failure"],
            "p_failure_ci95": r["p_failure_ci95"],
            "basis": "measured" if trusted else f"pooled (fewer than {MIN_ALERTS} alerts)",
        }
    return {
        "version": version,
        "horizon_hours": cal["horizon_hours"],
        "provenance": {
            "source": source,
            "method": "P(failure of the alert's mode within the horizon | rule opened), "
            "right-censored, Wilson 95% CI; simulated fleet, not field data",
            "run": backtest.get("run", {}),
        },
        "fallback": {"p_failure": pooled, "hours_to_failure_p10": None},
        "rules": dict(sorted(out_rules.items())),
    }


def holdout_check(calibration: dict, holdout: dict) -> dict:  # type: ignore[type-arg]
    """Score the fitted probabilities on a run with a different seed.

    Brier score = mean (p - outcome)^2 over held-out alerts (0 = perfect). It is
    compared with a no-skill forecast that predicts the held-out base rate for
    every alert: calibration only helps if it beats that.
    """
    rules = holdout["calibration"]["rules"]
    n = sum(r["eligible_alerts"] for r in rules.values())
    if n == 0:
        return {"alerts": 0}
    fb = calibration["fallback"]["p_failure"]
    base = sum(r["followed_by_failure"] for r in rules.values()) / n
    brier = no_skill = 0.0
    per_rule = {}
    for code, r in rules.items():
        k, m = r["followed_by_failure"], r["eligible_alerts"]
        p = calibration["rules"].get(code, {}).get("p_failure", fb)
        brier += k * (1 - p) ** 2 + (m - k) * p**2
        no_skill += k * (1 - base) ** 2 + (m - k) * base**2
        lo, hi = r["p_failure_ci95"]
        per_rule[code] = {
            "fitted_p": p, "holdout_p": r["p_failure"], "holdout_ci95": [lo, hi],
            "fitted_within_holdout_ci": lo <= p <= hi, "holdout_alerts": m,
        }  # fmt: skip
    return {
        "alerts": n,
        "brier": round(brier / n, 4),
        "brier_no_skill_base_rate": round(no_skill / n, 4),
        "holdout_base_rate": round(base, 3),
        "rules": per_rule,
    }


def main(argv: list[str]) -> int:
    holdout_path = None
    if "--holdout" in argv:
        i = argv.index("--holdout")
        holdout_path = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2 :]
    src, dst = Path(argv[0]), Path(argv[1])
    version = argv[2] if len(argv) > 2 else "v1"
    result = build(json.loads(src.read_text()), str(src), version)
    if holdout_path is not None:
        check = holdout_check(result, json.loads(holdout_path.read_text()))
        result["provenance"]["holdout"] = {"source": str(holdout_path), **check}
        print(json.dumps(check, indent=1))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["rules"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

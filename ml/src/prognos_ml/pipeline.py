"""M8 end to end: datasets -> features -> LightGBM vs calibrated rules -> evidence.

    uv run prognos-ml generate --name train_a --seed 42            # ~15 min each
    uv run prognos-ml run --data ml/data/m8 --train train_a,train_b \\
        --test test_heldout,test_slow,test_rare \\
        --model-dir ml/models/failure-7d-v1 --output evidence/benchmarks/m8-model-vs-baseline.json

Train and test runs never share a seed, so test vehicles, their faults and their
noise are all unseen. `test_slow` (different degradation speed) and `test_rare`
(4x fewer faults) are shifted variants: they check that a gain is not an
artefact of one simulator setting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from prognos_ml import model as m
from prognos_ml.dataset import RunConfig, generate
from prognos_ml.features import FEATURES as FEATURE_NAMES
from prognos_ml.features import FEATURES_VERSION, FeatureConfig, build


def features_for(run_dir: Path, cfg: FeatureConfig) -> pa.Table:
    spec = {**cfg.__dict__, "features_version": FEATURES_VERSION}
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:10]
    cache = run_dir / f"features-{key}.parquet"
    if cache.exists() and cache.stat().st_mtime >= (run_dir / "run.json").stat().st_mtime:
        return pq.read_table(cache)
    table = build(run_dir, cfg)
    pq.write_table(table, cache)
    return table


def run(
    data: Path, train_runs: list[str], test_runs: list[str], model_dir: Path | None,
    *, capacity_share: float = 0.05, feature_cfg: FeatureConfig | None = None,
) -> dict[str, Any]:  # fmt: skip
    cfg = feature_cfg or FeatureConfig()
    started = time.perf_counter()
    train_table = pa.concat_tables([features_for(data / r, cfg) for r in train_runs])
    trained = m.train(train_table)
    results: dict[str, Any] = {}
    for name in test_runs:
        table = features_for(data / name, cfg)
        info = json.loads((data / name / "run.json").read_text())
        scale = float(info["config"]["time_scale"])
        x = m.matrix(table)
        model_prob = trained.predict(x)
        base_prob = m.baseline_probability(table, trained.no_alert_rate)
        base_rank = table.column("baseline_score").to_numpy()
        results[name] = {
            "run": info["config"],
            "model": m.evaluate(
                table, model_prob, model_prob, time_scale=scale, capacity_share=capacity_share
            ),
            "baseline_rules": m.evaluate(
                table, base_rank, base_prob, time_scale=scale, capacity_share=capacity_share
            ),
            "ap_difference_model_minus_baseline": m.bootstrap_ap_difference(
                table, model_prob, base_rank
            ),
        }
    heldout = features_for(data / test_runs[0], cfg)
    x_held = m.matrix(heldout)
    probs = trained.predict(x_held)
    top_rows, seen = [], set()
    for i in probs.argsort()[::-1]:  # the 5 highest-risk rows, one per vehicle
        vid = heldout.column("vehicle_id")[int(i)].as_py()
        if vid not in seen:
            seen.add(vid)
            top_rows.append(i)
        if len(top_rows) == 5:
            break
    examples = [
        {
            "vehicle_id": heldout.column("vehicle_id")[int(i)].as_py(),
            "p_failure_7d": round(float(probs[i]), 3),
            "label": int(heldout.column("label")[int(i)].as_py()),
            "next_failure_mode": heldout.column("next_failure_mode")[int(i)].as_py(),
            "top_contributions": m.explain(trained, x_held[i : i + 1])[0],
        }
        for i in top_rows
    ]
    importance = sorted(
        zip(FEATURE_NAMES, trained.booster.feature_importance("gain"), strict=True),
        key=lambda kv: -kv[1],
    )
    total_gain = sum(g for _, g in importance) or 1.0
    result: dict[str, Any] = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "machine": platform.machine(),
            "system": platform.system(),
            "cpu_count": os.cpu_count(),
            "note": "not the target Mac",
        },
        "label": "ground-truth FAILURE of any mode within 168 h real time after the snapshot",
        "feature_config": cfg.__dict__,
        "train": {
            "runs": train_runs,
            "rows": trained.train_rows,
            "positive_rate": round(trained.train_positive_rate, 4),
            "best_iteration": trained.best_iteration,
            "baseline_no_alert_rate": round(trained.no_alert_rate, 4),
        },
        "test": results,
        "feature_importance_gain_share": {
            name: round(float(gain) / total_gain, 4) for name, gain in importance[:15]
        },
        "explanations_top5_heldout": examples,
        "inference_latency": m.latency(trained, x_held),
        "wall_seconds": round(time.perf_counter() - started, 1),
    }
    if model_dir is not None:
        result["artefact"] = {
            k: v
            for k, v in m.save(
                trained,
                model_dir,
                {
                    "name": "failure-7d",
                    "version": model_dir.name,
                    "trained_at": result["measured_at"],
                    "train_runs": train_runs,
                    "label": result["label"],
                    "feature_config": cfg.__dict__,
                    "features_version": FEATURES_VERSION,
                },
            ).items()
            if k in {"name", "version", "model_sha256", "model_bytes", "best_iteration"}
        } | {"path": str(model_dir)}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prognos-ml", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="simulate one run into Parquet")
    gen.add_argument("--name", required=True)
    gen.add_argument("--data", type=Path, default=Path("ml/data/m8"))
    gen.add_argument("--vehicles", type=int, default=300)
    gen.add_argument("--hours", type=float, default=8.0)
    gen.add_argument("--fault-rate", type=float, default=0.2)
    gen.add_argument("--time-scale", type=float, default=48.0)
    gen.add_argument("--seed", type=int, required=True)
    gen.add_argument("--report-interval", type=float, default=1.0,
                     help="seconds between reports per vehicle (1 = 1 Hz)")  # fmt: skip
    ev = sub.add_parser("run", help="train on some runs, evaluate on others")
    ev.add_argument("--data", type=Path, default=Path("ml/data/m8"))
    ev.add_argument("--train", required=True, help="comma-separated run names")
    ev.add_argument("--test", required=True, help="comma-separated run names (first = main)")
    ev.add_argument("--model-dir", type=Path)
    ev.add_argument("--capacity-share", type=float, default=0.05)
    ev.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        info = generate(
            RunConfig(
                args.name,
                args.vehicles,
                args.hours,
                args.fault_rate,
                args.time_scale,
                args.seed,
                args.report_interval,
            ),
            args.data / args.name,
        )
        print(json.dumps(info, indent=2))
        return 0
    result = run(
        args.data,
        args.train.split(","),
        args.test.split(","),
        args.model_dir,
        capacity_share=args.capacity_share,
    )
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""LightGBM 7-day failure model and its evaluation against the calibrated-rules baseline.

Both scorers are evaluated on exactly the same rows with the same metrics:
  * ranking:  average precision (PR-AUC) and ROC-AUC over all snapshots
  * probability quality: Brier score. The baseline's probability is the M7
    calibrated rule probability when an alert is open, else the training-set
    failure rate of rows with no open alert (a fair, calibrated baseline).
  * capacity: at every snapshot, the workshop can take the top K vehicles
    (K = capacity_share of the fleet). Reported: mean precision of that list,
    and the share of failures whose vehicle entered the list at least 48 h
    (real time) before breaking down, with the median lead time.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pyarrow as pa
from numpy.typing import NDArray
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from prognos_common.catalog import VEHICLE_MODELS, Powertrain
from prognos_ml.features import CATEGORICAL_FEATURES, FEATURES, NUMERIC_FEATURES

CATEGORIES: dict[str, list[str]] = {
    "model_code": [m.model_code for m in VEHICLE_MODELS],
    "powertrain": [p.value for p in Powertrain],
}
PARAMS: dict[str, Any] = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 7,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 2,
    "verbosity": -1,
}
F64 = NDArray[np.float64]


def matrix(table: pa.Table) -> F64:
    """Feature matrix in FEATURES order; categoricals as fixed integer codes, nulls as NaN."""
    cols = []
    for name in NUMERIC_FEATURES:
        cols.append(table.column(name).to_numpy(zero_copy_only=False).astype(np.float64))
    for name in CATEGORICAL_FEATURES:
        index = {v: i for i, v in enumerate(CATEGORIES[name])}
        values = table.column(name).to_pylist()
        cols.append(np.array([index.get(v, np.nan) for v in values], dtype=np.float64))
    return np.column_stack(cols)


def labels(table: pa.Table) -> NDArray[np.int8]:
    return np.asarray(table.column("label").to_numpy(), dtype=np.int8)


def _vehicle_split(table: pa.Table, share: float, seed: int) -> tuple[NDArray[Any], NDArray[Any]]:
    """Validation = a random share of *vehicles* (never the same vehicle on both sides)."""
    vids = np.array(table.column("vehicle_id").to_pylist())
    unique = np.unique(vids)
    rng = np.random.default_rng(seed)
    held = set(rng.choice(unique, size=max(1, int(len(unique) * share)), replace=False))
    mask = np.array([v in held for v in vids])
    return ~mask, mask


@dataclass
class TrainedModel:
    booster: lgb.Booster
    best_iteration: int
    no_alert_rate: float  # baseline probability for rows without an open alert
    train_rows: int
    train_positive_rate: float

    def predict(self, x: F64) -> F64:
        return np.asarray(self.booster.predict(x, num_iteration=self.best_iteration))

    def contributions(self, x: F64) -> F64:
        """Per-feature contributions (TreeSHAP, log-odds); the last column is the bias."""
        return np.asarray(
            self.booster.predict(x, num_iteration=self.best_iteration, pred_contrib=True)
        )


def train(table: pa.Table, *, rounds: int = 2000, seed: int = 7) -> TrainedModel:
    x, y = matrix(table), labels(table)
    fit, val = _vehicle_split(table, 0.2, seed)
    cat_idx = [FEATURES.index(c) for c in CATEGORICAL_FEATURES]
    train_set = lgb.Dataset(x[fit], y[fit], feature_name=FEATURES, categorical_feature=cat_idx,
                            free_raw_data=False)  # fmt: skip
    val_set = lgb.Dataset(x[val], y[val], reference=train_set)
    booster = lgb.train(
        {**PARAMS, "seed": seed, "metric": ["average_precision", "binary_logloss"]},
        train_set,
        num_boost_round=rounds,
        valid_sets=[val_set],
        callbacks=[lgb.early_stopping(100, first_metric_only=True, verbose=False)],
    )
    rules_open = table.column("rules_open").to_numpy()
    no_alert = y[rules_open == 0]
    return TrainedModel(
        booster=booster,
        best_iteration=booster.best_iteration or rounds,
        no_alert_rate=float(no_alert.mean()) if len(no_alert) else 0.0,
        train_rows=len(y),
        train_positive_rate=float(y.mean()),
    )


def baseline_probability(table: pa.Table, no_alert_rate: float) -> F64:
    score = table.column("baseline_score").to_numpy().astype(np.float64)
    rules_open = table.column("rules_open").to_numpy()
    rule_p = np.clip(score - 0.001 * np.minimum(rules_open, 50), 0.0, 1.0)
    return np.where(rules_open > 0, rule_p, no_alert_rate)


def _stable_tiebreak(vehicle_ids: list[str]) -> F64:
    return np.array(
        [int(hashlib.blake2b(v.encode(), digest_size=4).hexdigest(), 16) / 2**32
         for v in vehicle_ids]
    )  # fmt: skip


def capacity_metrics(
    table: pa.Table, score: F64, *, capacity_share: float, time_scale: float,
    min_lead_hours: float = 48.0,
) -> dict[str, Any]:  # fmt: skip
    """Top-K list per snapshot; precision of the list and failures caught >= 48 h ahead."""
    t = table.column("t").to_numpy()
    vids = table.column("vehicle_id").to_pylist()
    y = labels(table)
    nxt = table.column("next_failure_ts").to_numpy(zero_copy_only=False)
    tie = _stable_tiebreak(vids)
    min_lead_s = min_lead_hours * 3600.0 / time_scale

    precisions: list[float] = []
    best_lead: dict[tuple[str, float], float] = {}
    failures = {(vids[i], float(nxt[i])) for i in np.flatnonzero(y == 1)}
    order = np.lexsort((tie, -score, t))  # by time, then score desc, then stable tie-break
    boundaries = np.flatnonzero(np.diff(t[order])) + 1
    for group in np.split(order, boundaries):
        k = max(1, round(capacity_share * len(group)))
        top = group[:k]
        precisions.append(float(y[top].mean()))
        for i in top:
            if y[i] == 1:
                key = (vids[i], float(nxt[i]))
                lead = float(nxt[i]) - float(t[i])
                best_lead[key] = max(best_lead.get(key, 0.0), lead)
    caught = [lead for lead in best_lead.values() if lead >= min_lead_s]
    all_leads_h = [lead * time_scale / 3600.0 for lead in best_lead.values()]
    return {
        "capacity_share": capacity_share,
        "snapshots": len(precisions),
        "mean_precision_at_capacity": round(statistics.fmean(precisions), 4),
        "failures": len(failures),
        "failures_listed_before_breakdown": len(best_lead),
        f"recall_at_{int(min_lead_hours)}h": round(len(caught) / len(failures), 4)
        if failures else None,
        "median_lead_hours_real": round(statistics.median(all_leads_h), 1) if all_leads_h else None,
    }  # fmt: skip


def evaluate(
    table: pa.Table, score: F64, prob: F64, *, time_scale: float, capacity_share: float
) -> dict[str, Any]:
    y = labels(table)
    return {
        "rows": len(y),
        "positive_rate": round(float(y.mean()), 4),
        "average_precision": round(float(average_precision_score(y, score)), 4),
        "roc_auc": round(float(roc_auc_score(y, score)), 4),
        "brier": round(float(brier_score_loss(y, np.clip(prob, 0, 1))), 4),
        **capacity_metrics(table, score, capacity_share=capacity_share, time_scale=time_scale),
    }


def bootstrap_ap_difference(
    table: pa.Table, model_score: F64, baseline_score: F64, *, n: int = 200, seed: int = 11
) -> dict[str, float]:
    """95% interval of AP(model) - AP(baseline), resampling whole vehicles (rows are correlated)."""
    y = labels(table)
    vids = np.array(table.column("vehicle_id").to_pylist())
    unique, inverse = np.unique(vids, return_inverse=True)
    rows_by_vehicle = [np.flatnonzero(inverse == i) for i in range(len(unique))]
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n):
        pick = rng.integers(0, len(unique), len(unique))
        idx = np.concatenate([rows_by_vehicle[i] for i in pick])
        if y[idx].min() == y[idx].max():
            continue
        diffs.append(
            average_precision_score(y[idx], model_score[idx])
            - average_precision_score(y[idx], baseline_score[idx])
        )
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"mean": round(float(np.mean(diffs)), 4), "ci95_low": round(float(lo), 4),
            "ci95_high": round(float(hi), 4), "resamples": len(diffs)}  # fmt: skip


def explain(model: TrainedModel, x: F64, top: int = 3) -> list[list[tuple[str, float]]]:
    """Top contributing features per row (signed log-odds contribution)."""
    contrib = model.contributions(x)[:, :-1]
    out = []
    for row in contrib:
        idx = np.argsort(-np.abs(row))[:top]
        out.append([(FEATURES[i], round(float(row[i]), 3)) for i in idx])
    return out


def latency(model: TrainedModel, x: F64, *, single_calls: int = 1000) -> dict[str, Any]:
    """Inference cost: one vehicle at a time (API path) and a batch (fleet re-score)."""
    rows = x[:single_calls]
    for i in range(min(50, len(rows))):  # warm-up: first calls pay one-off setup
        model.predict(rows[i : i + 1])
    times = []
    for i in range(len(rows)):
        t0 = time.perf_counter()
        model.predict(rows[i : i + 1])
        times.append(time.perf_counter() - t0)
    times.sort()
    batch = np.tile(x, (math.ceil(100_000 / len(x)), 1))[:100_000]
    t0 = time.perf_counter()
    model.predict(batch)
    batch_s = time.perf_counter() - t0
    return {
        "single_row_ms_p50": round(times[len(times) // 2] * 1000, 3),
        "single_row_ms_p99": round(times[int(len(times) * 0.99) - 1] * 1000, 3),
        "batch_100k_rows_seconds": round(batch_s, 3),
        "batch_rows_per_second": round(100_000 / batch_s),
        "threads": PARAMS["num_threads"],
    }


def save(model: TrainedModel, directory: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "model.txt"
    model.booster.save_model(str(path), num_iteration=model.best_iteration)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    meta = {
        **metadata,
        "features": FEATURES,
        "categories": CATEGORIES,
        "params": PARAMS,
        "best_iteration": model.best_iteration,
        "no_alert_rate": model.no_alert_rate,
        "model_sha256": digest,
        "model_bytes": path.stat().st_size,
    }
    (directory / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def load(directory: Path) -> TrainedModel:
    meta = json.loads((directory / "metadata.json").read_text())
    booster = lgb.Booster(model_file=str(directory / "model.txt"))
    if hashlib.sha256((directory / "model.txt").read_bytes()).hexdigest() != meta["model_sha256"]:
        raise ValueError(f"model file in {directory} does not match its metadata checksum")
    if meta["features"] != FEATURES:
        raise ValueError("model was trained on a different feature list")
    return TrainedModel(booster, meta["best_iteration"], meta["no_alert_rate"], 0, 0.0)

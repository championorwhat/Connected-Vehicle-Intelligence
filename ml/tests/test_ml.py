"""M8: dataset, features (no look-ahead, correct labels), model, metrics, artefact."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from prognos_ml import model as m
from prognos_ml.dataset import RunConfig, generate
from prognos_ml.features import FEATURES, FeatureConfig, build
from prognos_ml.pipeline import run

CFG = FeatureConfig(snapshot_s=60, long_window_s=600, short_window_s=120)


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("m8")
    for name, seed in (("train", 5), ("test", 6)):
        generate(RunConfig(name, vehicles=60, hours=1.0, fault_rate=0.5, time_scale=240.0,
                           seed=seed), root / name)  # fmt: skip
    return root


@pytest.fixture(scope="module")
def table(data: Path) -> pa.Table:
    return build(data / "train", CFG)


def test_dataset_files_and_counts(data: Path) -> None:
    info = json.loads((data / "train" / "run.json").read_text())
    events = pq.read_table(data / "train" / "events.parquet")
    assert events.num_rows == info["canonical_events"] > 0
    assert info["failures"] > 0
    assert pq.read_table(data / "train" / "vehicles.parquet").num_rows == 60


def test_window_features_use_only_past_events(data: Path, table: pa.Table) -> None:
    events = pq.read_table(data / "train" / "events.parquet", columns=["vehicle_id", "ts"])
    ev_vid = np.array(events.column("vehicle_id").to_pylist())
    ev_ts = events.column("ts").to_numpy()
    rng = np.random.default_rng(0)
    for i in rng.choice(table.num_rows, 25, replace=False):
        vid = table.column("vehicle_id")[int(i)].as_py()
        t = table.column("t")[int(i)].as_py()
        # buckets are whole minutes strictly before t
        expected = np.sum((ev_vid == vid) & (ev_ts >= t - CFG.long_window_s) & (ev_ts < t))
        assert table.column("events_long")[int(i)].as_py() == expected


def test_labels_match_ground_truth_and_downtime_is_excluded(data: Path, table: pa.Table) -> None:
    truth = pq.read_table(data / "train" / "truth.parquet").to_pylist()
    failures = [(r["vehicle_id"], r["failure_ts"]) for r in truth if r["type"] == "FAILURE"]
    info = json.loads((data / "train" / "run.json").read_text())
    horizon = 168 * 3600 / info["config"]["time_scale"]
    downtime = 48 * 3600 / info["config"]["time_scale"]
    for row in table.select(["vehicle_id", "t", "label"]).to_pylist():
        mine = [f for v, f in failures if v == row["vehicle_id"]]
        assert row["label"] == int(any(row["t"] < f <= row["t"] + horizon for f in mine))
        assert not any(f <= row["t"] < f + downtime for f in mine)
    assert 0 < pc.sum(table.column("label")).as_py() < table.num_rows


def test_capacity_metrics_on_hand_built_case() -> None:
    # 2 snapshots x 4 vehicles; capacity 25% -> top 1 per snapshot
    t = pa.table({
        "t": [0.0] * 4 + [60.0] * 4,
        "vehicle_id": ["a", "b", "c", "d"] * 2,
        "label": pa.array([1, 0, 0, 0, 1, 0, 1, 0], pa.int8()),
        "next_failure_ts": [7200.0, None, None, None, 7200.0, None, 200.0, None],
    })  # fmt: skip
    score = np.array([0.9, 0.1, 0.2, 0.3, 0.1, 0.2, 0.9, 0.3])
    r = m.capacity_metrics(t, score, capacity_share=0.25, time_scale=1.0, min_lead_hours=1.0)
    assert r["mean_precision_at_capacity"] == 1.0
    assert r["failures"] == 2
    # a listed at t=0, 7200 s ahead (>= 1 h); c listed at t=60, only 140 s ahead
    assert r["recall_at_1h"] == 0.5


def test_baseline_probability_uses_rule_p_or_no_alert_rate() -> None:
    t = pa.table({"baseline_score": [0.0, 0.952, 1.002], "rules_open": [0, 2, 2]})
    assert m.baseline_probability(t, 0.1).tolist() == pytest.approx([0.1, 0.95, 1.0])


def test_train_explain_save_load(tmp_path: Path, table: pa.Table) -> None:
    trained = m.train(table, rounds=50)
    x = m.matrix(table)
    assert x.shape == (table.num_rows, len(FEATURES))
    p = trained.predict(x)
    assert ((p >= 0) & (p <= 1)).all()
    names = {name for row in m.explain(trained, x[:3]) for name, _ in row}
    assert names <= set(FEATURES)
    meta = m.save(trained, tmp_path / "v-test", {"name": "failure-7d"})
    loaded = m.load(tmp_path / "v-test")
    assert np.allclose(loaded.predict(x), p)
    assert meta["model_sha256"]
    with (tmp_path / "v-test" / "model.txt").open("a") as fh:
        fh.write("\n# tampered\n")
    with pytest.raises(ValueError, match="checksum"):
        m.load(tmp_path / "v-test")


def test_pipeline_compares_model_and_baseline_on_same_rows(data: Path, tmp_path: Path) -> None:
    result = run(data, ["train"], ["test"], tmp_path / "model", feature_cfg=CFG)
    test = result["test"]["test"]
    assert test["model"]["rows"] == test["baseline_rules"]["rows"] > 0
    for scorer in ("model", "baseline_rules"):
        assert 0 <= test[scorer]["average_precision"] <= 1
    assert result["inference_latency"]["batch_rows_per_second"] > 0
    assert (tmp_path / "model" / "metadata.json").exists()

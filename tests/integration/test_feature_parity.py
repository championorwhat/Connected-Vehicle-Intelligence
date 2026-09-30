"""Training/serving parity: ClickHouse buckets == DuckDB buckets, same features, same scores.

The model is trained on features computed by DuckDB from Parquet; live scoring computes
the minute buckets inside ClickHouse. This test loads identical canonical events into
both engines and requires the same buckets, the same window features and the same
predictions, then runs a full scoring cycle into Redis and ClickHouse.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pyarrow.parquet as pq
import pytest
import redis as redis_lib

from prognos_ml import model as m
from prognos_ml.dataset import RunConfig, generate
from prognos_ml.features import (
    BUCKET_COLUMNS,
    FEATURES,
    FeatureConfig,
    _bucket_sql,
    bucket_sql_clickhouse,
    build,
    features_at,
)
from prognos_ml.scorer import score_once

CFG = FeatureConfig(snapshot_s=60, long_window_s=600, short_window_s=120)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, ch: Any) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("parity") / "run"
    # 1 h at 480x: the 7-day label horizon (1260 s simulated) fits inside the run.
    info = generate(RunConfig("parity", vehicles=40, hours=1.0, fault_rate=0.5,
                              time_scale=480.0, seed=9), out)  # fmt: skip
    vehicles = pq.read_table(out / "vehicles.parquet")
    tenant = dict(zip(vehicles.column("vehicle_id").to_pylist(),
                      vehicles.column("tenant_id").to_pylist(), strict=True))  # fmt: skip
    events = pq.read_table(out / "events.parquet").to_pylist()
    ch.command("TRUNCATE TABLE events")
    cols = ["event_id", "tenant_id", "vehicle_id", "seq", "event_ts", "ingest_ts", "event_type",
            "speed_kmh", "odometer_km", "ignition_on", "engine_rpm", "coolant_temp_c",
            "lv_battery_v", "hv_soh_pct", "hv_pack_temp_c", "hv_cell_delta_mv", "tyre_fl_kpa",
            "tyre_fr_kpa", "tyre_rl_kpa", "tyre_rr_kpa", "dtc_codes"]  # fmt: skip
    rows = []
    for i, e in enumerate(events):
        ts = dt.datetime.fromtimestamp(e["ts"], dt.UTC)
        rows.append([
            uuid.uuid4(), uuid.UUID(tenant[e["vehicle_id"]]), uuid.UUID(e["vehicle_id"]), i, ts, ts,
            e["event_type"], e["speed_kmh"], e["odometer_km"] or 0.0, e["ignition_on"],
            None if e["engine_rpm"] is None else int(e["engine_rpm"]), e["coolant_temp_c"],
            e["lv_battery_v"], e["hv_soh_pct"], e["hv_pack_temp_c"],
            None if e["hv_cell_delta_mv"] is None else int(e["hv_cell_delta_mv"]),
            e["tyre_fl_kpa"], e["tyre_fr_kpa"], e["tyre_rl_kpa"], e["tyre_rr_kpa"],
            e["dtc_codes"],
        ])  # fmt: skip
    ch.insert("events", rows, column_names=cols)
    return {"dir": out, "info": info, "vehicles": vehicles}


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return bool(a == b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and math.isnan(a):
        return isinstance(b, float) and math.isnan(b)
    # ClickHouse stores signals as Float32; training reads JSON floats as float64.
    return math.isclose(float(a), float(b), rel_tol=1e-4, abs_tol=1e-4)


def test_minute_buckets_match(run: dict[str, Any], ch: Any) -> None:
    start, end = run["info"]["start_ts"], run["info"]["end_ts"] + 60
    con = duckdb.connect()
    con.execute(_bucket_sql(str(run["dir"] / "events.parquet")))
    duck = {(r[0], r[1]): r for r in con.execute(
        f"SELECT {', '.join(BUCKET_COLUMNS)} FROM buckets").fetchall()}  # fmt: skip
    result = ch.query(bucket_sql_clickhouse(start - 60, end))
    house = {(r[0], r[1]): r for r in result.result_rows}
    assert list(result.column_names) == BUCKET_COLUMNS
    assert duck.keys() == house.keys()
    assert len(duck) > 500
    mismatches = [
        (key, BUCKET_COLUMNS[j], duck[key][j], house[key][j])
        for key in duck
        for j in range(2, len(BUCKET_COLUMNS))
        if not _close(duck[key][j], house[key][j])
    ]
    assert not mismatches, mismatches[:5]


def test_features_and_predictions_match(run: dict[str, Any], ch: Any) -> None:
    t = math.floor(run["info"]["end_ts"] / 60) * 60
    vehicles = run["vehicles"].select(["vehicle_id", "model_code", "powertrain"])

    duck = duckdb.connect()
    duck.execute(_bucket_sql(str(run["dir"] / "events.parquet")))
    duck.register("vehicles", vehicles)
    offline = features_at(duck, t, CFG)

    live = duckdb.connect()
    live.register("buckets", ch.query_arrow(bucket_sql_clickhouse(t - 600, t), use_strings=True))
    live.register("vehicles", vehicles)
    online = features_at(live, t, CFG)

    assert offline.column("vehicle_id").to_pylist() == online.column("vehicle_id").to_pylist()
    for name in FEATURES:
        a, b = offline.column(name).to_pylist(), online.column(name).to_pylist()
        bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b, strict=True)) if not _close(x, y)]
        assert not bad, (name, bad[:3])

    trained = m.train(build(run["dir"], CFG), rounds=40)
    p_off, p_on = trained.predict(m.matrix(offline)), trained.predict(m.matrix(online))
    assert np.allclose(p_off, p_on, atol=1e-3)


def test_scoring_cycle_publishes_ranking_and_history(
    run: dict[str, Any], ch: Any, redis_url: tuple[str, int], tmp_path: Path
) -> None:
    trained = m.train(build(run["dir"], CFG), rounds=40)
    cache = redis_lib.Redis(host=redis_url[0], port=redis_url[1])
    ch.command("TRUNCATE TABLE risk_scores")
    end = run["info"]["end_ts"]
    summary = score_once(ch, "", cache, trained, "failure-7d-test", now=end + 60, lag_s=60,
                         shards=2, cfg=CFG, vehicles=run["vehicles"], min_minutes=8)  # fmt: skip
    assert summary["vehicles_scored"] == 40
    tenant = run["vehicles"].column("tenant_id")[0].as_py()
    ranking = cache.zrevrange(f"tenant:{tenant}:risk", 0, -1, withscores=True)
    assert ranking
    first = ranking[0][0]
    top = cache.get(f"veh:{first.decode() if isinstance(first, bytes) else first}:risk")
    assert top is not None
    assert b'"model_version":"failure-7d-test"' in top
    stored = ch.query(
        "SELECT count(), uniqExact(vehicle_id), min(toUnixTimestamp(scored_at)) FROM risk_scores"
    ).result_rows[0]
    assert stored == (40, 40, math.floor(end / 60) * 60)  # stored at the snapshot time

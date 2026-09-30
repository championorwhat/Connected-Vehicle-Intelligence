"""prognos-scorer: live 7-day failure risk for every vehicle, every few minutes.

    prognos-scorer                      # every SCORER_INTERVAL_SECONDS (default 300)
    prognos-scorer --once --output score.json

One cycle, for snapshot time t (a minute boundary, SCORER_LAG_SECONDS behind now so
events that arrive up to that late are included):
  1. ClickHouse computes per-vehicle minute buckets for [t - 60 min, t)
     (features.bucket_sql_clickhouse, parity-tested against the training SQL);
  2. DuckDB turns them into window features with the *same* SQL used for training
     (features.window_sql via features_at);
  3. LightGBM scores them; TreeSHAP explains the riskier ones;
  4. results go to Redis (veh:{id}:risk, tenant:{tid}:risk ranking swapped in
     atomically with RENAME) and to ClickHouse risk_scores (history).
Vehicles are processed in hash shards (SCORER_SHARDS) to bound memory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import clickhouse_connect
import duckdb
import numpy as np
import orjson
import psycopg
import pyarrow as pa
import pyarrow.compute as pc
import redis
from prometheus_client import Gauge, Histogram, start_http_server

from prognos_ml import model as m
from prognos_ml.features import FeatureConfig, bucket_sql_clickhouse, features_at

log = logging.getLogger("prognos_ml")

SCORED = Gauge("prognos_scorer_vehicles_scored", "Vehicles scored in the last cycle")
CYCLE = Histogram("prognos_scorer_cycle_seconds", "Duration of one scoring cycle",
                  buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300))  # fmt: skip
EXPLAIN_ABOVE = 0.2  # TreeSHAP only for rows worth explaining (it is the costly part)

_VEHICLES = """
    SELECT v.vehicle_id::text AS vehicle_id, v.tenant_id::text AS tenant_id,
           v.model_code::text AS model_code, m.powertrain
    FROM vehicles v JOIN vehicle_models m USING (model_code)
    WHERE v.status <> 'retired'
"""


def score_once(
    ch: Any, pg_dsn: str, cache: redis.Redis | None, trained: m.TrainedModel, version: str,
    *, now: float | None = None, lag_s: float = 120.0, shards: int = 1,
    cfg: FeatureConfig | None = None, ttl_s: int = 1800, vehicles: pa.Table | None = None,
    min_events: int = 30, min_minutes: int = 45,
) -> dict[str, Any]:  # fmt: skip
    cfg = cfg or FeatureConfig()
    started = time.perf_counter()
    now = time.time() if now is None else now
    t = math.floor((now - lag_s) / 60) * 60
    if vehicles is None:
        with psycopg.connect(pg_dsn) as conn:
            rows = conn.execute(_VEHICLES).fetchall()
        vehicles = pa.table({
            "vehicle_id": [r[0] for r in rows], "tenant_id": [r[1] for r in rows],
            "model_code": [r[2] for r in rows], "powertrain": [r[3] for r in rows],
        })  # fmt: skip
    tenant_of = dict(zip(vehicles.column("vehicle_id").to_pylist(),
                         vehicles.column("tenant_id").to_pylist(), strict=True))  # fmt: skip
    con = duckdb.connect()
    con.register("vehicles", vehicles)

    results: list[dict[str, Any]] = []
    insufficient = 0
    for shard in range(shards):
        buckets = ch.query_arrow(
            bucket_sql_clickhouse(t - cfg.long_window_s, t, shard, shards),
            use_strings=True,
        )
        con.register("buckets", buckets)
        feats = features_at(con, t, cfg)
        # Data-sufficiency gate: the model was trained on full 60-minute windows. With
        # little data (few events, or only a few minutes covered) trend slopes are fitted
        # to a handful of noisy points and the score is an extrapolation (seen in M9), so
        # such vehicles get no score and the planner/API fall back to the rules.
        enough = pc.and_(
            pc.greater_equal(feats.column("events_long"), min_events),
            pc.greater_equal(feats.column("minutes_long"), min_minutes),
        )
        insufficient += feats.num_rows - pc.sum(enough).as_py()
        feats = feats.filter(enough)
        if feats.num_rows == 0:
            con.unregister("buckets")
            continue
        x = m.matrix(feats)
        probs = trained.predict(x)
        explain_rows = np.flatnonzero(probs >= EXPLAIN_ABOVE)
        reasons = dict(zip(explain_rows.tolist(), m.explain(trained, x[explain_rows]),
                           strict=True)) if len(explain_rows) else {}  # fmt: skip
        vids = feats.column("vehicle_id").to_pylist()
        for i, vid in enumerate(vids):
            results.append({
                "vehicle_id": vid, "tenant_id": tenant_of.get(vid),
                "probability": round(float(probs[i]), 4),
                "top_features": reasons.get(i, []),
            })  # fmt: skip
        con.unregister("buckets")

    scored_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    if cache is not None:
        _publish(cache, results, version, scored_at, ttl_s)
    if results:
        # A datetime, not a number: a numeric value in a DateTime64(3) column is read as
        # milliseconds (-> 1970), and the table's TTL then silently deletes the row.
        scored_dt = dt.datetime.fromtimestamp(t, dt.UTC)
        ch.insert(
            "risk_scores",
            [[r["tenant_id"], r["vehicle_id"], scored_dt, version, "ANY", r["probability"],
              float("nan"),  # expected cost avoided: NOT computed (costs are placeholders)
              {k: float(v) for k, v in r["top_features"]}] for r in results if r["tenant_id"]],
            column_names=["tenant_id", "vehicle_id", "scored_at", "model_version",
                          "failure_mode", "probability", "expected_cost_avoided",
                          "top_features"],
        )  # fmt: skip
    elapsed = time.perf_counter() - started
    SCORED.set(len(results))
    CYCLE.observe(elapsed)
    probs_all = [r["probability"] for r in results]
    return {
        "snapshot_ts": scored_at,
        "model_version": version,
        "vehicles_registered": vehicles.num_rows,
        "vehicles_scored": len(results),
        "vehicles_insufficient_data": insufficient,
        "gate": {"min_events": min_events, "min_minutes_with_data": min_minutes},
        "above_0_5": sum(p >= 0.5 for p in probs_all),
        "shards": shards,
        "cycle_seconds": round(elapsed, 3),
    }


def _publish(
    cache: redis.Redis, results: list[dict[str, Any]], version: str, scored_at: str, ttl_s: int
) -> None:
    by_tenant: dict[str, dict[str, float]] = defaultdict(dict)
    pipe = cache.pipeline(transaction=False)
    for r in results:
        if not r["tenant_id"]:
            continue
        by_tenant[r["tenant_id"]][r["vehicle_id"]] = r["probability"]
        pipe.set(f"veh:{r['vehicle_id']}:risk", orjson.dumps({
            "probability": r["probability"], "model_version": version, "scored_at": scored_at,
            "top_features": [{"feature": f, "contribution": c} for f, c in r["top_features"]],
        }), ex=ttl_s)  # fmt: skip
    pipe.execute()
    for tenant, scores in by_tenant.items():
        # Build the new ranking under a temporary key and swap it in: readers never see a
        # half-written ranking, and vehicles that stopped reporting drop out.
        tmp = f"tenant:{tenant}:risk:building"
        pipe = cache.pipeline(transaction=True)
        pipe.delete(tmp)
        pipe.zadd(tmp, scores)
        pipe.expire(tmp, ttl_s)
        pipe.rename(tmp, f"tenant:{tenant}:risk")
        pipe.execute()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prognos-scorer", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s")
    e = os.environ
    model_dir = Path(e.get("MODEL_DIR", "ml/models/failure-7d-v3"))
    trained = m.load(model_dir)
    version = json.loads((model_dir / "metadata.json").read_text())["version"]
    ch = clickhouse_connect.get_client(
        host=e.get("CLICKHOUSE_HOST", "localhost"), port=int(e.get("CLICKHOUSE_HTTP_PORT", "8123")),
        username=e.get("CLICKHOUSE_USER", "prognos"), password=e.get("CLICKHOUSE_PASSWORD", ""),
        database=e.get("CLICKHOUSE_DB", "telemetry"),
    )  # fmt: skip
    dsn = (f"host={e.get('POSTGRES_HOST', 'localhost')} port={e.get('POSTGRES_PORT', '5432')} "
           f"dbname={e.get('POSTGRES_DB', 'prognos')} user={e.get('POSTGRES_USER', 'prognos')} "
           f"password={e.get('POSTGRES_PASSWORD', '')}")  # fmt: skip
    cache = redis.Redis(
        host=e.get("REDIS_HOST", "localhost"), port=int(e.get("REDIS_PORT", "6379")),
        password=e.get("REDIS_PASSWORD") or None, socket_timeout=10,
    )  # fmt: skip
    interval = float(e.get("SCORER_INTERVAL_SECONDS", "300"))
    shards = int(e.get("SCORER_SHARDS", "1"))
    lag = float(e.get("SCORER_LAG_SECONDS", "120"))
    min_events = int(e.get("SCORER_MIN_EVENTS", "30"))
    min_minutes = int(e.get("SCORER_MIN_MINUTES", "45"))
    if not args.once and (port := int(e.get("METRICS_PORT", "9106"))):
        start_http_server(port)

    stop = False

    def _stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stop:
        try:
            summary = score_once(ch, dsn, cache, trained, version, lag_s=lag, shards=shards,
                                 min_events=min_events, min_minutes=min_minutes)  # fmt: skip
            log.info("scored: %s", summary)
            if args.output:
                args.output.write_text(json.dumps(summary, indent=2) + "\n")
        except Exception:
            if args.once:
                raise
            log.exception("scoring cycle failed; retrying next interval")
        if args.once:
            break
        deadline = time.monotonic() + interval
        while not stop and time.monotonic() < deadline:
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Training table: one row per (vehicle, snapshot time), built with DuckDB over Parquet.

1. Minute buckets per vehicle (one scan of events.parquet): means, extremes and
   counts of the physical signals, residuals against the driving state, and DTC
   counts per failure mode. 8.6 M events -> ~150 K buckets.
2. Snapshot grid every `snapshot_s`; window features over the buckets in
   [t - window, t): a long window (trend: mean, extreme, least-squares slope) and
   a short window (current level). No bucket at or after t is used, so there is
   no look-ahead.
3. Label: the vehicle has a ground-truth FAILURE (any mode) in (t, t + horizon],
   horizon = 7 days of real time (168 h / time_scale of simulated time).
   Excluded rows: while the vehicle is broken down (failure .. + max downtime),
   and the last `horizon` of the run (outcome not observable: right-censoring).
4. Baseline score: the M7 calibrated-rules risk of the vehicle's open alerts at t
   (max calibrated P(fail in 7 d), tie-broken by how many are open), i.e. exactly
   what the planner ranks by today.

Windows are in simulated time. With time_scale 48, 60 simulated minutes cover the
last 48 h of degradation but only 60 minutes of driving; see the caveats in
docs/ml/model-card.md.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from prognos_common.catalog import DTCS, FAILURE_MODES
from prognos_sim.fleet import DOWNTIME_HOURS
from prognos_stream.planner import Calibration

MODE_DTCS = {fm.code: [d.code for d in DTCS if d.failure_mode == fm.code] for fm in FAILURE_MODES}
OTHER_DTCS = [d.code for d in DTCS if d.failure_mode is None]

# Model inputs. `model_code` and `powertrain` are categorical.
# odometer_km and model_year are kept in the table but NOT used: the simulator's fault
# hazard does not depend on mileage or age, so in this data they only identify vehicles
# (a memorisation risk). In a real fleet they are candidate features to test.
NUMERIC_FEATURES = [
    "events_long", "ignition_share_long",
    "coolant_mean_long", "coolant_max_long", "coolant_resid_long", "coolant_resid_short",
    "coolant_resid_slope_h",
    "rpm_resid_sd_long", "rpm_resid_sd_max_long", "rpm_resid_sd_short",
    "lv_rest_long", "lv_rest_short", "lv_min_long", "lv_mean_slope_h",
    "tyre_ratio_long", "tyre_ratio_min_long", "tyre_ratio_min_short", "tyre_ratio_slope_h",
    "cell_delta_long", "cell_delta_max_long", "cell_delta_short", "cell_delta_slope_h",
    "pack_temp_max_long", "soh_long",
    "dtc_events_long", "dtc_events_short",
    *[f"dtc_{fm.code.lower()}_long" for fm in FAILURE_MODES],
    *[f"dtc_{fm.code.lower()}_short" for fm in FAILURE_MODES],
    "dtc_other_long",
]  # fmt: skip
CATEGORICAL_FEATURES = ["model_code", "powertrain"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True)
class FeatureConfig:
    snapshot_s: float = 300.0
    long_window_s: float = 3600.0
    short_window_s: float = 600.0
    horizon_hours_real: float = 168.0


def _sql_list(codes: list[str]) -> str:
    return "[" + ", ".join(f"'{c}'" for c in codes) + "]"


def _bucket_sql(events: str) -> str:
    per_mode = ",\n".join(
        f"count(*) FILTER (list_has_any(dtc_codes, {_sql_list(codes)}))::DOUBLE"
        f" AS dtc_{mode.lower()}"
        for mode, codes in MODE_DTCS.items()
    )
    tyre_min = "least(tyre_fl_kpa, tyre_fr_kpa, tyre_rl_kpa, tyre_rr_kpa)"
    tyre_max = "greatest(tyre_fl_kpa, tyre_fr_kpa, tyre_rl_kpa, tyre_rr_kpa)"
    return f"""
    CREATE TABLE buckets AS
    SELECT vehicle_id,
           floor(ts / 60) * 60 AS m,
           count(*)::DOUBLE AS n,
           count(*) FILTER (ignition_on)::DOUBLE AS n_ign,
           avg(coolant_temp_c) FILTER (ignition_on) AS coolant_mean,
           max(coolant_temp_c) AS coolant_max,
           avg(coolant_temp_c - (88 + 0.03 * speed_kmh)) FILTER (ignition_on) AS coolant_resid,
           stddev_pop(engine_rpm - (750 + 28 * speed_kmh))
               FILTER (ignition_on AND engine_rpm > 0) AS rpm_resid_sd,
           avg(lv_battery_v) FILTER (NOT ignition_on) AS lv_rest,
           min(lv_battery_v) AS lv_min,
           avg(lv_battery_v) AS lv_mean,
           avg({tyre_min} / nullif({tyre_max}, 0)) AS tyre_ratio,
           min({tyre_min} / nullif({tyre_max}, 0)) AS tyre_ratio_min,
           avg(hv_cell_delta_mv) AS cell_delta,
           max(hv_cell_delta_mv) AS cell_delta_max,
           max(hv_pack_temp_c) AS pack_temp_max,
           avg(hv_soh_pct) AS soh,
           max(odometer_km) AS odometer_km,
           count(*) FILTER (len(dtc_codes) > 0)::DOUBLE AS dtc_events,
           {per_mode},
           count(*) FILTER (list_has_any(dtc_codes, {_sql_list(OTHER_DTCS)}))::DOUBLE AS dtc_other
    FROM read_parquet('{events}')
    GROUP BY ALL
    """


def build(run_dir: Path, cfg: FeatureConfig | None = None) -> pa.Table:
    """Feature table for one generated run (see module docstring)."""
    cfg = cfg or FeatureConfig()
    info: dict[str, Any] = json.loads((run_dir / "run.json").read_text())
    scale = float(info["config"]["time_scale"])
    start, end = float(info["start_ts"]), float(info["end_ts"])
    horizon = cfg.horizon_hours_real * 3600.0 / scale
    downtime = DOWNTIME_HOURS[1] * 3600.0 / scale
    calibration = Calibration.load()
    L, S = cfg.long_window_s, cfg.short_window_s

    con = duckdb.connect()
    con.execute(_bucket_sql(str(run_dir / "events.parquet")))
    con.execute(
        f"CREATE TABLE vehicles AS SELECT * FROM read_parquet('{run_dir}/vehicles.parquet')"
    )
    con.execute(f"CREATE TABLE truth AS SELECT * FROM read_parquet('{run_dir}/truth.parquet')")
    con.execute(f"CREATE TABLE alerts AS SELECT * FROM read_parquet('{run_dir}/alerts.parquet')")
    con.execute("CREATE TABLE rule_p (rule_code VARCHAR, p DOUBLE)")
    con.executemany(
        "INSERT INTO rule_p VALUES (?, ?)",
        [(code, r.p_failure) for code, r in calibration.rules.items()],
    )
    for name, value in cfg.__dict__.items():
        if name.endswith("_s") and value % 60:
            raise ValueError(f"{name} must be a whole number of minutes (buckets are 1 min)")
    # Snapshots sit on minute boundaries, so every bucket with m < t ends by t: no
    # bucket can contain an event from after the snapshot (no look-ahead).
    first = math.ceil((start + L) / 60) * 60  # warm-up: a full long window first
    grid = f"{int(first)}, {int(end - horizon)}, {int(cfg.snapshot_s)}"
    con.execute(f"""
        CREATE TABLE snaps AS
        SELECT v.vehicle_id, g.t
        FROM vehicles v,
             (SELECT unnest(range({grid}))::DOUBLE AS t) g
    """)
    per_mode_long = ",\n".join(
        f"sum(b.dtc_{m.lower()}) AS dtc_{m.lower()}_long,"
        f" coalesce(sum(b.dtc_{m.lower()}) FILTER (b.m >= s.t - {S}), 0) AS dtc_{m.lower()}_short"
        for m in MODE_DTCS
    )
    sql = f"""
    WITH feats AS (
        SELECT s.vehicle_id, s.t,
               sum(b.n) AS events_long,
               sum(b.n_ign) / nullif(sum(b.n), 0) AS ignition_share_long,
               max(b.odometer_km) AS odometer_km,
               avg(b.coolant_mean) AS coolant_mean_long,
               max(b.coolant_max) AS coolant_max_long,
               avg(b.coolant_resid) AS coolant_resid_long,
               avg(b.coolant_resid) FILTER (b.m >= s.t - {S}) AS coolant_resid_short,
               regr_slope(b.coolant_resid, b.m) * 3600 AS coolant_resid_slope_h,
               avg(b.rpm_resid_sd) AS rpm_resid_sd_long,
               max(b.rpm_resid_sd) AS rpm_resid_sd_max_long,
               avg(b.rpm_resid_sd) FILTER (b.m >= s.t - {S}) AS rpm_resid_sd_short,
               avg(b.lv_rest) AS lv_rest_long,
               avg(b.lv_rest) FILTER (b.m >= s.t - {S}) AS lv_rest_short,
               min(b.lv_min) AS lv_min_long,
               regr_slope(b.lv_mean, b.m) * 3600 AS lv_mean_slope_h,
               avg(b.tyre_ratio) AS tyre_ratio_long,
               min(b.tyre_ratio_min) AS tyre_ratio_min_long,
               min(b.tyre_ratio_min) FILTER (b.m >= s.t - {S}) AS tyre_ratio_min_short,
               regr_slope(b.tyre_ratio, b.m) * 3600 AS tyre_ratio_slope_h,
               avg(b.cell_delta) AS cell_delta_long,
               max(b.cell_delta_max) AS cell_delta_max_long,
               avg(b.cell_delta) FILTER (b.m >= s.t - {S}) AS cell_delta_short,
               regr_slope(b.cell_delta, b.m) * 3600 AS cell_delta_slope_h,
               max(b.pack_temp_max) AS pack_temp_max_long,
               avg(b.soh) AS soh_long,
               sum(b.dtc_events) AS dtc_events_long,
               coalesce(sum(b.dtc_events) FILTER (b.m >= s.t - {S}), 0) AS dtc_events_short,
               {per_mode_long},
               sum(b.dtc_other) AS dtc_other_long
        FROM snaps s
        JOIN buckets b ON b.vehicle_id = s.vehicle_id AND b.m >= s.t - {L} AND b.m < s.t
        GROUP BY s.vehicle_id, s.t
    ),
    open_alerts AS (
        SELECT o.vehicle_id, o.rule_code, o.ts AS opened,
               (SELECT min(c.ts) FROM alerts c
                WHERE c.fingerprint = o.fingerprint AND c.status = 'cleared' AND c.ts >= o.ts)
                   AS cleared
        FROM alerts o
        WHERE o.status = 'open' AND o.failure_mode IS NOT NULL
    ),
    baseline AS (
        SELECT s.vehicle_id, s.t,
               max(coalesce(r.p, {calibration.fallback.p_failure})) AS rule_p_max,
               count(*) AS rules_open
        FROM snaps s
        JOIN open_alerts a ON a.vehicle_id = s.vehicle_id AND a.opened < s.t
             AND (a.cleared IS NULL OR a.cleared >= s.t)
        LEFT JOIN rule_p r ON r.rule_code = a.rule_code
        GROUP BY s.vehicle_id, s.t
    ),
    failures AS (SELECT vehicle_id, failure_mode, failure_ts FROM truth WHERE type = 'FAILURE')
    SELECT f.*, v.model_code, v.powertrain, v.model_year::DOUBLE AS model_year,
           coalesce(bl.rule_p_max, 0) + 0.001 * least(coalesce(bl.rules_open, 0), 50)
               AS baseline_score,
           coalesce(bl.rules_open, 0) AS rules_open,
           (SELECT min(x.failure_ts) FROM failures x
            WHERE x.vehicle_id = f.vehicle_id AND x.failure_ts > f.t
              AND x.failure_ts <= f.t + {horizon}) AS next_failure_ts,
           (SELECT arg_min(x.failure_mode, x.failure_ts) FROM failures x
            WHERE x.vehicle_id = f.vehicle_id AND x.failure_ts > f.t
              AND x.failure_ts <= f.t + {horizon}) AS next_failure_mode
    FROM feats f
    JOIN vehicles v USING (vehicle_id)
    LEFT JOIN baseline bl ON bl.vehicle_id = f.vehicle_id AND bl.t = f.t
    WHERE NOT EXISTS (
        SELECT 1 FROM failures x
        WHERE x.vehicle_id = f.vehicle_id
          AND f.t >= x.failure_ts AND f.t < x.failure_ts + {downtime})
    ORDER BY f.t, f.vehicle_id
    """
    table = con.execute(sql).to_arrow_table()
    label = pc.is_valid(table.column("next_failure_ts")).cast(pa.int8())
    table = table.append_column("label", label)
    meta = {
        "run": info["config"]["name"],
        "time_scale": scale,
        "horizon_s_sim": horizon,
        "feature_config": cfg.__dict__,
        "calibration_version": calibration.version,
    }
    return table.replace_schema_metadata({"prognos": json.dumps(meta)})

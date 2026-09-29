-- migrate:up
-- =============================================================================
-- Prognos telemetry store (ClickHouse). See docs/database/data-model.md.
--
-- events: one row per canonical telemetry event. The sort key starts with
-- tenant_id then vehicle_id, so tenant- and vehicle-scoped scans read contiguous
-- granules. ReplacingMergeTree collapses exact replays (same tenant, vehicle,
-- event_ts, seq) during merges. That is the storage-level safety net behind the
-- stream processor's dedup; queries needing exact results use FINAL.
-- =============================================================================
CREATE TABLE IF NOT EXISTS events
(
    event_id         UUID,
    tenant_id        UUID,
    vehicle_id       UUID,
    vin              FixedString(17),
    oem              LowCardinality(String),
    schema_version   UInt8,
    seq              UInt64,
    event_ts         DateTime64(3, 'UTC') CODEC(DoubleDelta, ZSTD(1)),
    ingest_ts        DateTime64(3, 'UTC') CODEC(DoubleDelta, ZSTD(1)),
    event_type       LowCardinality(String),
    latitude         Float64 CODEC(Gorilla, ZSTD(1)),
    longitude        Float64 CODEC(Gorilla, ZSTD(1)),
    speed_kmh        Float32 CODEC(Gorilla, ZSTD(1)),
    heading_deg      UInt16,
    odometer_km      Float64 CODEC(Gorilla, ZSTD(1)),
    ignition_on      Bool,
    engine_rpm       Nullable(UInt16),
    coolant_temp_c   Nullable(Float32),
    fuel_level_pct   Nullable(Float32),
    lv_battery_v     Nullable(Float32),
    hv_soc_pct       Nullable(Float32),
    hv_soh_pct       Nullable(Float32),
    hv_pack_temp_c   Nullable(Float32),
    hv_cell_delta_mv Nullable(UInt16),
    tyre_fl_kpa      Nullable(Float32),
    tyre_fr_kpa      Nullable(Float32),
    tyre_rl_kpa      Nullable(Float32),
    tyre_rr_kpa      Nullable(Float32),
    dtc_codes        Array(LowCardinality(String))
)
ENGINE = ReplacingMergeTree(ingest_ts)
PARTITION BY toYYYYMMDD(event_ts)
ORDER BY (tenant_id, vehicle_id, event_ts, seq)
TTL toDateTime(event_ts) + INTERVAL 30 DAY DELETE
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;

-- Per-vehicle, per-minute rollup (warm tier, 400 days). Aggregates are chosen to
-- be idempotent under duplicate inserts where possible: min/max are naturally
-- idempotent and the event count uses uniqExact(seq) rather than count(). Averages
-- can be slightly biased by duplicates that slip past upstream dedup; this is
-- documented, and the events table remains the exact source.
CREATE TABLE IF NOT EXISTS vehicle_minute
(
    tenant_id        UUID,
    vehicle_id       UUID,
    minute           DateTime('UTC'),
    events           AggregateFunction(uniqExact, UInt64),
    max_speed_kmh    SimpleAggregateFunction(max, Float32),
    avg_speed_kmh    AggregateFunction(avg, Float32),
    max_odometer_km  SimpleAggregateFunction(max, Float64),
    max_coolant_c    SimpleAggregateFunction(max, Nullable(Float32)),
    avg_coolant_c    AggregateFunction(avg, Nullable(Float32)),
    max_engine_rpm   SimpleAggregateFunction(max, Nullable(UInt16)),
    min_lv_battery_v SimpleAggregateFunction(min, Nullable(Float32)),
    min_tyre_kpa     SimpleAggregateFunction(min, Nullable(Float32)),
    max_pack_temp_c  SimpleAggregateFunction(max, Nullable(Float32)),
    max_cell_delta_mv SimpleAggregateFunction(max, Nullable(UInt16)),
    min_soh_pct      SimpleAggregateFunction(min, Nullable(Float32)),
    dtc_codes        SimpleAggregateFunction(groupUniqArrayArray, Array(String))
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (tenant_id, vehicle_id, minute)
TTL minute + INTERVAL 400 DAY DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS vehicle_minute_mv TO vehicle_minute AS
SELECT
    tenant_id,
    vehicle_id,
    toStartOfMinute(toDateTime(event_ts)) AS minute,
    uniqExactState(seq) AS events,
    max(speed_kmh) AS max_speed_kmh,
    avgState(speed_kmh) AS avg_speed_kmh,
    max(odometer_km) AS max_odometer_km,
    max(coolant_temp_c) AS max_coolant_c,
    avgState(coolant_temp_c) AS avg_coolant_c,
    max(engine_rpm) AS max_engine_rpm,
    min(lv_battery_v) AS min_lv_battery_v,
    least(min(tyre_fl_kpa), min(tyre_fr_kpa), min(tyre_rl_kpa), min(tyre_rr_kpa)) AS min_tyre_kpa,
    max(hv_pack_temp_c) AS max_pack_temp_c,
    max(hv_cell_delta_mv) AS max_cell_delta_mv,
    min(hv_soh_pct) AS min_soh_pct,
    groupUniqArrayArray(dtc_codes) AS dtc_codes
FROM events
GROUP BY tenant_id, vehicle_id, minute;

-- Model outputs over time (explainability + model monitoring), 180 days.
CREATE TABLE IF NOT EXISTS risk_scores
(
    tenant_id             UUID,
    vehicle_id            UUID,
    scored_at             DateTime64(3, 'UTC'),
    model_version         LowCardinality(String),
    failure_mode          LowCardinality(String),
    probability           Float32,
    expected_cost_avoided Float32,
    top_features          Map(String, Float32)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(scored_at)
ORDER BY (tenant_id, vehicle_id, scored_at, failure_mode)
TTL toDateTime(scored_at) + INTERVAL 180 DAY DELETE;

-- Rejected payloads for inspection and replay (7 days).
CREATE TABLE IF NOT EXISTS dlq_events
(
    received_at  DateTime64(3, 'UTC'),
    oem          LowCardinality(String),
    reason       LowCardinality(String),
    vehicle_hint String,
    payload      String CODEC(ZSTD(3))
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(received_at)
ORDER BY (reason, received_at)
TTL toDateTime(received_at) + INTERVAL 7 DAY DELETE;

-- migrate:down
DROP TABLE IF EXISTS vehicle_minute_mv;
DROP TABLE IF EXISTS vehicle_minute;
DROP TABLE IF EXISTS risk_scores;
DROP TABLE IF EXISTS dlq_events;
DROP TABLE IF EXISTS events;

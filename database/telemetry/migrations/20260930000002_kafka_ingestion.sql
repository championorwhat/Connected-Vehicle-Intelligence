-- migrate:up
-- =============================================================================
-- Kafka -> ClickHouse ingestion without a custom sink service.
--
-- A Kafka-engine table is a *consumer* (group `clickhouse`); a materialized view
-- moves each consumed block into the MergeTree table. Offsets are committed
-- after the block is written, so delivery is at-least-once; duplicates collapse
-- in ReplacingMergeTree (events) or are harmless in the append-only history.
-- {{KAFKA_BROKERS}} is substituted by migrate.sh from $KAFKA_BROKERS.
-- =============================================================================

-- ignition_on can be unknown (OEM did not report it); keep that distinct from false.
ALTER TABLE events MODIFY COLUMN IF EXISTS ignition_on Nullable(Bool);

CREATE TABLE IF NOT EXISTS canonical_queue
(
    event_id         String,
    tenant_id        String,
    vehicle_id       String,
    vin              String,
    oem              String,
    schema_version   UInt8,
    seq              UInt64,
    event_ts         String,
    ingest_ts        String,
    event_type       String,
    latitude         Float64,
    longitude        Float64,
    speed_kmh        Float32,
    heading_deg      Nullable(UInt16),
    odometer_km      Nullable(Float64),
    ignition_on      Nullable(Bool),
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
    dtc_codes        Array(String)
)
ENGINE = Kafka
SETTINGS kafka_broker_list = '{{KAFKA_BROKERS}}',
         kafka_topic_list = 'telemetry.canonical',
         kafka_group_name = 'clickhouse',
         kafka_format = 'JSONEachRow',
         kafka_num_consumers = 1,
         kafka_max_block_size = 65536,
         kafka_skip_broken_messages = 100,
         kafka_handle_error_mode = 'stream',
         input_format_skip_unknown_fields = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS canonical_to_events TO events AS
SELECT
    toUUID(event_id)                         AS event_id,
    toUUID(tenant_id)                        AS tenant_id,
    toUUID(vehicle_id)                       AS vehicle_id,
    toFixedString(vin, 17)                   AS vin,
    oem,
    schema_version,
    seq,
    parseDateTime64BestEffort(event_ts, 3)   AS event_ts,
    parseDateTime64BestEffort(ingest_ts, 3)  AS ingest_ts,
    event_type,
    latitude,
    longitude,
    speed_kmh,
    ifNull(heading_deg, 0)                   AS heading_deg,
    ifNull(odometer_km, 0)                   AS odometer_km,
    ignition_on,
    engine_rpm,
    coolant_temp_c,
    fuel_level_pct,
    lv_battery_v,
    hv_soc_pct,
    hv_soh_pct,
    hv_pack_temp_c,
    hv_cell_delta_mv,
    tyre_fl_kpa,
    tyre_fr_kpa,
    tyre_rl_kpa,
    tyre_rr_kpa,
    dtc_codes
FROM canonical_queue
WHERE length(_error) = 0;

-- Messages ClickHouse could not parse are kept for inspection, not silently dropped.
CREATE TABLE IF NOT EXISTS ingestion_errors
(
    received_at DateTime DEFAULT now(),
    topic       LowCardinality(String),
    partition   UInt64,
    offset      UInt64,
    error       String,
    raw         String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY (received_at, topic)
TTL received_at + INTERVAL 7 DAY DELETE;

CREATE MATERIALIZED VIEW IF NOT EXISTS canonical_errors TO ingestion_errors AS
SELECT _topic AS topic, _partition AS partition, _offset AS offset, _error AS error,
       _raw_message AS raw
FROM canonical_queue
WHERE length(_error) > 0;

-- Alert history for analytics (the transactional copy lives in PostgreSQL).
CREATE TABLE IF NOT EXISTS alert_history
(
    fingerprint  FixedString(32),
    status       LowCardinality(String),
    tenant_id    UUID,
    vehicle_id   UUID,
    rule_code    LowCardinality(String),
    severity     LowCardinality(String),
    failure_mode LowCardinality(Nullable(String)),
    event_ts     DateTime64(3, 'UTC'),
    detected_at  DateTime64(3, 'UTC'),
    value        Float64
)
ENGINE = ReplacingMergeTree(detected_at)
PARTITION BY toYYYYMM(event_ts)
ORDER BY (tenant_id, vehicle_id, fingerprint, status)
TTL toDateTime(event_ts) + INTERVAL 400 DAY DELETE;

CREATE TABLE IF NOT EXISTS alerts_queue
(
    fingerprint  String,
    status       String,
    tenant_id    String,
    vehicle_id   String,
    rule_code    String,
    severity     String,
    failure_mode Nullable(String),
    event_ts     String,
    detected_at  String,
    value        Float64
)
ENGINE = Kafka
SETTINGS kafka_broker_list = '{{KAFKA_BROKERS}}',
         kafka_topic_list = 'alerts',
         kafka_group_name = 'clickhouse-alerts',
         kafka_format = 'JSONEachRow',
         kafka_skip_broken_messages = 100,
         input_format_skip_unknown_fields = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS alerts_to_history TO alert_history AS
SELECT
    toFixedString(fingerprint, 32)            AS fingerprint,
    status,
    toUUID(tenant_id)                         AS tenant_id,
    toUUID(vehicle_id)                        AS vehicle_id,
    rule_code,
    severity,
    failure_mode,
    parseDateTime64BestEffort(event_ts, 3)    AS event_ts,
    parseDateTime64BestEffort(detected_at, 3) AS detected_at,
    value
FROM alerts_queue;

-- migrate:down
DROP TABLE IF EXISTS alerts_to_history;
DROP TABLE IF EXISTS alerts_queue;
DROP TABLE IF EXISTS alert_history;
DROP TABLE IF EXISTS canonical_errors;
DROP TABLE IF EXISTS ingestion_errors;
DROP TABLE IF EXISTS canonical_to_events;
DROP TABLE IF EXISTS canonical_queue;

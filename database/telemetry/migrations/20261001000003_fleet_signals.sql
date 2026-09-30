-- migrate:up
-- Emerging-fault radar signals (topic fleet.signals) for the API. Aggregates only:
-- no vehicle or tenant identifiers. ReplacingMergeTree on (window, cohort, DTC)
-- collapses a signal re-emitted after a radar restart.
CREATE TABLE IF NOT EXISTS fleet_signals
(
    window_start      DateTime('UTC'),
    window_end        DateTime('UTC'),
    level             LowCardinality(String),
    dtc               LowCardinality(String),
    oem               LowCardinality(String),
    model_code        LowCardinality(String),
    firmware_version  String,               -- '' for model-level signals
    powertrain        LowCardinality(String),
    affected_vehicles UInt32,
    cohort_vehicles   UInt32,
    rate              Float64,
    baseline_rate     Float64,
    rate_ratio        Float64,
    p_adjusted        Float64,
    received_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(received_at)
ORDER BY (model_code, firmware_version, dtc, level, window_start)
TTL window_start + INTERVAL 400 DAY DELETE;

CREATE TABLE IF NOT EXISTS signals_queue
(
    window_start      Float64,
    window_end        Float64,
    level             String,
    dtc               String,
    oem               String,
    model_code        String,
    firmware_version  Nullable(String),
    powertrain        String,
    affected_vehicles UInt32,
    cohort_vehicles   UInt32,
    rate              Float64,
    baseline_rate     Float64,
    rate_ratio        Float64,
    p_adjusted        Float64
)
ENGINE = Kafka
SETTINGS kafka_broker_list = '{{KAFKA_BROKERS}}',
         kafka_topic_list = 'fleet.signals',
         kafka_group_name = 'clickhouse-signals',
         kafka_format = 'JSONEachRow',
         kafka_skip_broken_messages = 100,
         input_format_skip_unknown_fields = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS signals_to_table TO fleet_signals AS
SELECT
    toDateTime(toUInt32(window_start), 'UTC') AS window_start,
    toDateTime(toUInt32(window_end), 'UTC')   AS window_end,
    level, dtc, oem, model_code,
    ifNull(firmware_version, '')              AS firmware_version,
    powertrain, affected_vehicles, cohort_vehicles, rate, baseline_rate, rate_ratio, p_adjusted
FROM signals_queue;

-- migrate:down
DROP TABLE IF EXISTS signals_to_table;
DROP TABLE IF EXISTS signals_queue;
DROP TABLE IF EXISTS fleet_signals;

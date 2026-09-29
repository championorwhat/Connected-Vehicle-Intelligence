# Data Model

## 1. Polyglot map

| Data | Store | Why this store | CAP / PACELC | Retention |
|---|---|---|---|---|
| Tenants, fleets, vehicles, drivers, users/RBAC, cost inputs | PostgreSQL | Relational integrity, ACID, low volume (~300K rows at 100K vehicles) | **CP / EC**: one primary, synchronous commit | Life of tenant |
| Alerts, work orders | PostgreSQL | Must never be lost or duplicated; state machine with constraints | **CP / EC** | 2 years (policy, M12) |
| Audit log | PostgreSQL (monthly partitions, append-only trigger) | Tamper-resistance, cheap partition drop for retention | **CP / EC** | Partition drop after policy period |
| Raw canonical telemetry | ClickHouse `events` | ~8.6 B rows/day at 100K ev/s; columnar compression; time-window scans | **AP / EL**: async replication; replays collapsed by ReplacingMergeTree | 30 days (hot) |
| Per-minute rollups | ClickHouse `vehicle_minute` | 1,440× fewer rows than raw; ML features and trends | AP / EL | 400 days (warm) |
| Risk scores | ClickHouse `risk_scores` | Append-only time series of model outputs | AP / EL | 180 days |
| Rejected payloads | ClickHouse `dlq_events` + Kafka `telemetry.dlq` | Inspection and replay | AP / EL | 7 days |
| Latest vehicle state, dedup windows, rate limits | Redis (M5/M6) | Sub-ms reads for 100K keys; rebuildable from Kafka | AP / EL | Ephemeral |
| Cold archive, training sets | Parquet at `OBJECT_STORE_URL` (M6/M8) | Cheapest storage; readable by DuckDB/Polars/Spark | n/a | Per policy |

## 2. Normalisation (3NF) and deliberate denormalisation

Every non-key attribute depends on the key, the whole key and nothing but the key:

- **1NF**: no repeating groups. A failure mode's applicable powertrains are a separate
  `failure_mode_powertrains` table, not an array column.
- **2NF**: composite-key tables (`role_permissions`, `user_roles`, `cost_parameters`,
  `failure_mode_powertrains`) carry no attribute that depends on only part of the key.
- **3NF**: no transitive dependencies. For example, a workshop's geohash is *not* stored,
  because it is determined by latitude/longitude; it is computed in the application.
  `vehicles.model_year` depends on the VIN, which is a candidate key, so it is allowed.

**Deliberate denormalisation (documented exception):** `tenant_id` is repeated on every
tenant-owned table. On `vehicles` it is transitively determined (`fleet_id → tenant_id`).
We keep it because:
1. row-level security (M12) and tenant-leading indexes need it without a join, and
2. every query in a multi-tenant API filters on it.

The anomaly risk (a vehicle pointing at another tenant's fleet) is removed by **composite
foreign keys**: `(fleet_id, tenant_id) → fleets(fleet_id, tenant_id)`, and the same for
workshops, drivers, alerts and work orders. The test
`test_vehicle_cannot_join_another_tenants_fleet` proves the database rejects it.

## 3. Constraints that encode business rules

| Rule | Mechanism | Test |
|---|---|---|
| VIN format (17 chars, no I/O/Q) | `CHECK` regex | `test_vin_check_constraint` |
| No cross-tenant references | composite FKs | `test_vehicle_cannot_join_another_tenants_fleet`, `test_work_order_cannot_use_another_tenants_workshop` |
| No overlapping subscriptions per tenant | `EXCLUDE USING gist` | `test_overlapping_subscriptions_rejected` |
| One driver per vehicle at a time (and vice versa) | two `EXCLUDE` constraints | `test_vehicle_cannot_have_two_drivers_at_once` |
| Drivers are pseudonymous | `CHECK (pseudonym ~ '^DRV-[0-9]{6}$')` | `test_driver_pseudonym_format_enforced` |
| Alert redelivery is idempotent | `UNIQUE (tenant_id, fingerprint)` + `ON CONFLICT DO NOTHING` | `test_alert_upsert_is_idempotent` |
| One active work order per vehicle + failure mode | partial unique index | `test_one_active_work_order_per_vehicle_and_mode` |
| State-machine consistency (ack ⇒ timestamp, completed ⇒ outcome) | `CHECK` | `test_acknowledged_alert_needs_timestamp`, `test_completed_work_order_needs_outcome` |
| Cost numbers must cite a source | `CHECK (length(source) > 0)` | `test_cost_parameters_require_provenance` |
| Audit log is append-only | `BEFORE UPDATE OR DELETE` trigger | `test_audit_log_is_partitioned_and_append_only`, `test_audit_log_delete_denied` |
| Every FK has a supporting index | catalog query | `test_every_foreign_key_is_indexed` |

## 4. Index strategy

M2 creates only **integrity indexes**: primary keys, unique constraints, business-rule partial
indexes and one index per foreign key. Query-shape indexes (composite, partial, covering) are
deliberately left to **M15**, where each one is justified by an `EXPLAIN ANALYZE` before/after
measurement. Adding them now would leave nothing honest to optimise.

## 5. ClickHouse design notes

- **Sort key `(tenant_id, vehicle_id, event_ts, seq)`**: tenant-scoped and vehicle-scoped
  range scans read contiguous granules. Leading with the tenant also supports per-tenant
  retention and erasure.
- **Partition by day, `ttl_only_drop_parts = 1`**: expiry drops whole parts instead of
  rewriting them.
- **Codecs**: `DoubleDelta` for monotonically increasing timestamps, `Gorilla` for slowly
  changing floats (GPS, speed, odometer), `LowCardinality` for OEM/event type/DTC strings.
- **Duplicates**:
  - Exact replays inside one insert block are collapsed at write time.
  - Replays across blocks collapse on merge (`ReplacingMergeTree(ingest_ts)`, latest copy wins).
  - `FINAL` gives exact reads before a merge.
  - The rollup uses `uniqExact(seq)` and min/max so it stays correct under duplicates.
  - All of this is verified in `tests/integration/test_clickhouse_schema.py`.

## 6. Capacity estimate (arithmetic only; storage figures are NOT YET MEASURED)

| Quantity | Value | Basis |
|---|---|---|
| Events/day at 100K ev/s | 8.64 × 10⁹ | 100,000 × 86,400 |
| Raw JSON volume/day | ~8.6 TB | brief's ~1 KB/event |
| Rollup rows/day | 1.44 × 10⁸ | 100K vehicles × 1,440 minutes |
| ClickHouse compressed bytes/event | **NOT YET MEASURED** | measured in M6 from `system.parts` on simulator data |
| Kafka bytes/day after lz4 | **NOT YET MEASURED** | measured in M4 from broker log size |

PostgreSQL at 100K vehicles (measured in the evidence below): `vehicles` 32 MB,
`drivers` 23 MB, `driver_assignments` 26 MB.

## 7. Evidence

- Seed of 100,000 vehicles:
  [`evidence/benchmarks/m2-seed-100k.json`](../../evidence/benchmarks/m2-seed-100k.json)
  (measured in a 4-vCPU Linux container, not the target Mac).
- Schema tests: `uv run pytest tests/integration` (26 tests, real PostgreSQL 17 and ClickHouse 25.8).

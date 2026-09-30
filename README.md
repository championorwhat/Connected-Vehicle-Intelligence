# Prognos: Predictive Maintenance for Connected Vehicle Fleets

> Know which vehicle fails next, and what it will cost if you wait.

Built for the **Connected Vehicle Intelligence Hackathon** by **Pratibimb Gupta**
(RA2311003010027).

**Status:** M9 (API with auth, RBAC and tenant isolation; live model scoring in shadow mode) complete. See [milestones](#milestones).
Every number in this repository is either measured (with a link to the evidence) or marked
**NOT YET MEASURED**.

---

## 1. Problem

A fleet maintenance manager running a 100,000-vehicle mixed ICE/EV fleet needs a way to know
**which vehicles are likely to fail in the next 7 days, and why**, early enough to book them into
a workshop. Today, faults are discovered as roadside breakdowns or warning lights that nobody can
triage at that scale.

Full analysis and decision matrix: [`docs/product/m0-problem-selection.md`](docs/product/m0-problem-selection.md).

## 2. Solution (target architecture)

```
Simulator (100K vehicles, 3 OEM formats, faults, dup/late/malformed)
   │ Kafka protocol
   ▼
Kafka ── telemetry.raw ─► [normalizer] ─► telemetry.canonical ─► [detector] ─► alerts / vehicle.risk
                              │ invalid ─► telemetry.dlq             │
                              ▼                                      ▼
            ClickHouse (telemetry)   PostgreSQL (alerts, work orders, audit)   Redis (live state)
                              │                                      │
                              └────────► FastAPI (REST + WebSocket) ◄┘ ──► React dashboard
Batch: Parquet → DuckDB → LightGBM (7-day failure model)   Ops: Prometheus · Grafana · OTel
```

## 3. Quick start (macOS)

Prerequisites: Docker Desktop (Memory set to **5 GB**), `uv`, Node 22. Check them with:

```zsh
make doctor
```

Start the stack:

```zsh
make env        # creates .env from .env.example
make bootstrap  # Python dev tools + git hooks
make up         # Kafka, PostgreSQL, ClickHouse, Redis (waits until healthy)
make ps
make topics     # shows the Kafka topics and partitions
make up-obs     # optional: + Prometheus (localhost:9090) and Grafana (localhost:3000)
make down       # stop (keeps data); `make clean` also deletes data
```

Run the same checks CI runs:

```zsh
make check             # lint, types, unit tests, compose validation (no Docker needed for tests)
make test-integration  # 53 tests against real PostgreSQL 17, ClickHouse 25.8, Kafka and Redis (Testcontainers)
```

## 4. Environment variables

All configuration lives in [`.env.example`](.env.example), with laptop-sized defaults.
Values tagged `[scale]` are raised for the 100K-vehicle runs.

| Variable | Default | Purpose |
|---|---|---|
| `VEHICLE_COUNT` | 10000 | Simulated vehicles (100000 for scale runs) |
| `EVENTS_PER_SECOND` | 10000 | Target aggregate rate |
| `BURST_MULTIPLIER` / `BURST_DURATION_SECONDS` | 3 / 300 | Burst profile from the brief |
| `DUPLICATE_RATE` / `OUT_OF_ORDER_RATE` / `MALFORMED_RATE` | 0.01 / 0.02 / 0.005 | Fault injection |
| `KAFKA_TELEMETRY_PARTITIONS` | 6 | Partitions for keyed telemetry topics |
| `KAFKA_REPLICATION_FACTOR` | 1 | 3 with the HA overlay |

## 5. Local resource profile

| Profile | Services | Memory limits (sum) | Measured idle use |
|---|---|---|---|
| core (`make up`) | Kafka, PostgreSQL, ClickHouse, Redis | 3.4 GB | ~0.7 GB ([evidence](evidence/benchmarks/m1-idle-memory.txt)) |
| + observability | + Prometheus, Grafana | 4.0 GB | ~0.8 GB |
| Kafka HA overlay | 3 brokers (RF=3, min ISR=2) | +1.5 GB | [broker-kill smoke test](evidence/chaos/m1-kafka-ha-smoke.md) |

Idle figures were measured in a Linux container, not yet on the target Mac.

## 6. Data model (M2)

- **PostgreSQL (3NF)**: 20 tables for tenants, fleets, vehicles, pseudonymous drivers, RBAC,
  alerts, work orders, cost inputs, erasure requests and an append-only partitioned audit log.
  Business rules live in the schema (composite tenant FKs, exclusion constraints, partial
  unique indexes) and are proven by tests. See [data-model.md](docs/database/data-model.md).
- **ClickHouse**: raw `events` (30-day hot tier, replay-collapsing), a duplicate-safe
  per-minute rollup (400 days), `risk_scores` and `dlq_events`.
- **Seed**: a deterministic roster shared with the simulator (`packages/common`). 100,000
  vehicles load in ~18 s ([evidence](evidence/benchmarks/m2-seed-100k.json), measured on a
  4-vCPU Linux container, not yet on the Mac).

## 7. Vehicle simulator (M3)

`apps/simulator` generates the whole fleet's telemetry. It is vectorised with numpy and sharded
across processes (`SIM_WORKERS`).

- **Three OEM formats:**
  - ORION: flat, metric units.
  - VEGA: nested, imperial units, IST timestamps, DTCs as a single string.
  - LYRA: list of named signals; bar, volts and epoch-millisecond timestamps.
- **Five failure modes with ground truth** (`sim.truth` topic): cooling, misfire, 12 V
  battery, tyre slow leak, and HV battery thermal.
- **Injected anomalies:** network latency, duplicates, out-of-order delivery, missing
  fields and 7 kinds of malformed payload, each counted for reconciliation.

Measured on a 4-vCPU Linux container (not yet on the Mac):
- **100K vehicles at 100K ev/s on one core.** 321K ev/s with 4 workers when not publishing.
- **Live run into Kafka:** 6.06 M messages at the target rate, with every message accounted
  for.
- **3× burst:** not sustained on 4 shared cores (173K ev/s), but with zero loss.

Details: [algorithms.md](docs/algorithms/algorithms.md).

## 8. Ingestion and normalisation (M4)

`apps/stream-processor` (`prognos-normalizer`) consumes `telemetry.raw` in a consumer group and
handles each message in order:

1. Maps each OEM format to the canonical, metric, UTC event (adapters per OEM and schema version).
2. Validates it: required fields, types, ranges, VIN check digit, clock skew.
3. Enriches it with `vehicle_id` and `tenant_id` from PostgreSQL.
4. Drops exact duplicates with a per-vehicle sliding sequence window.
5. Publishes to `telemetry.canonical`, or the original bytes to `telemetry.dlq` with a reason.

Offsets are committed only after every output is acknowledged (at-least-once delivery), and
each event carries a deterministic `event_id` for idempotent sinks. The contract is
[canonical-telemetry-v1.schema.json](packages/schemas/canonical-telemetry-v1.schema.json)
and [asyncapi.yaml](docs/api/asyncapi.yaml).

Measured on a 4-vCPU container shared with Kafka:
- **Throughput:** 25.8K msg/s per process, 62.9K msg/s with 3 processes.
- **Reconciliation:** exact over 1.19 M messages, including a simulator restart, with 0
  duplicate event_ids.
- **Ingest latency, on-time events:** p50 0.35 s, p95 1.04 s.

## 9. Real-time detection (M5)

`prognos-detector` consumes `telemetry.canonical` and keeps O(1) streaming state per vehicle.
It raises alerts in two tiers:
- **Critical, within seconds:** overheating, critical DTCs, tyre, HV and 12 V critical levels,
  and breakdowns.
- **Early warnings from trends:** coolant CUSUM, misfire roughness, resting 12 V voltage,
  tyre deficit against the other tyres (with estimated hours to critical), and HV cell imbalance.

Alerts use hysteresis and deterministic fingerprints (idempotent). The latest health snapshot
per vehicle goes to the compacted `vehicle.state` topic.

Measured:
- **Back-test against ground truth:** 98.6% of breakdowns warned, with a median
  lead of 49.6 h. Precision is 100% in the simulated
  world, which is not a field claim.
- **Live critical alert latency:** p50 0.495 s, p95 1.105 s,
  p99 3.592 s (target < 5 s).

Details and caveats: [detection-baseline.md](docs/performance/detection-baseline.md).

## 10. Persistence (M6)

- **ClickHouse** consumes `telemetry.canonical` and `alerts` directly (Kafka engine and
  materialised views); unparseable messages go to `ingestion_errors`.
- **`prognos-sink`** writes alerts idempotently to PostgreSQL and publishes live state to Redis:
  per-vehicle JSON, a per-tenant health ranking, a geo index, and alert pub/sub for the dashboard.
  It retries database outages with backoff and never commits offsets before the write.
- **`make reconcile`** proves no data loss end to end. Latest run: 1,807,966 raw → 1,772,080
  canonical = 1,772,080 unique rows in ClickHouse with 0 errors; 565 opened alerts = 565 rows in
  PostgreSQL ([evidence](evidence/benchmarks/m6-reconciliation.json)).
- Consistency per data class (CP vs AP): [ADR-004](docs/architecture/adr/ADR-004.md).

## 11. Core intelligence (M7)

- **Calibrated risk.** Each alert rule gets P(failure within 7 days) and a conservative
  deadline (the 10th-percentile time to failure). Both are measured against simulator
  ground truth, with Wilson intervals and right-censoring
  ([rules-v1.json](apps/stream-processor/src/prognos_stream/calibration/rules-v1.json)).
  Held-out check on another seed: 23 of 24 rules fall inside the held-out 95% interval.
  However, the probabilities do **not** beat a base-rate forecast (Brier 0.0226 vs 0.0187),
  because in the simulator almost every detected fault ends in failure. What the
  calibration adds is the per-rule deadlines. Real fleets will differ.
- **`prognos-planner`** turns open alerts into **proposed work orders**:
  - The most valuable vehicles go first, to the nearest same-tenant workshop with free
    capacity before the predicted failure. Bookings made after that point are flagged as
    late.
  - Proposals are idempotent (a partial unique index) and audited (`work_order.propose`).
  - **No fabricated money:** while repair costs are placeholders, ranking uses risk ×
    severity and `expected_cost_avoided` stays empty.
  - Measured on 100K vehicles with 5,000 open alerts: a planning cycle takes 0.9 s, and a
    re-run takes 28 ms and proposes nothing new
    ([evidence](evidence/benchmarks/m7-planner-100k.json)).
- **`prognos-radar`** finds a DTC that is suddenly common in one firmware release or model.
  It compares each cohort with its peers using an exact Poisson tail and a Bonferroni
  correction, and publishes the result to `fleet.signals`. In the back-test it caught the
  injected firmware defect in every window with no other signals, and raised 0 signals on
  the control run ([evidence](evidence/benchmarks/m7-radar-backtest.json)). On the full live
  stack (100K vehicles, 10K ev/s), it flagged only the defective cohort
  ([evidence](evidence/benchmarks/m7-radar-live.json)).
- Try it: `make pipeline-demo` (with the firmware defect), then `make plan` and `make signals-tail`.
- Details: [algorithms A8–A10](docs/algorithms/algorithms.md) and [ADR-006](docs/architecture/adr/ADR-006.md).

> **Costs needed:** real planned/unplanned repair and downtime costs (with a source) are
> required before any money figure is shown. Until then they are labelled placeholders.

## 12. Failure model (M8)

`ml/` (`prognos-ml`) builds a 7-day failure model and tests it against the M7 rules:

- **Data.** Five 8-hour simulated runs of 300 vehicles (about 8.5 M events each) go through
  the production normalizer and detector, and are stored as Parquet.
- **Features.** DuckDB computes per-minute buckets, then 60-minute and 10-minute window
  features. A test proves there is no look-ahead.
- **Model.** LightGBM, trained on two runs and tested on three unseen ones: a held-out run
  and two shifted variants (slower faults, rarer faults).
- **Held-out result** (same rows, same capacity rule for both scorers):

  | | Model | Rules (M7) |
  |---|---|---|
  | PR-AUC | **0.742** | 0.508 |
  | Precision of the top 5% list | **99.7%** | 72.5% |
  | Failures listed ≥ 48 h ahead | **34.7%** | 23.2% |

  The PR-AUC gain is +0.23, with a 95% interval of [0.17, 0.30]. The model also wins on
  both shifted sets ([evidence](evidence/benchmarks/m8-model-vs-baseline.json)).
- **Explanations** (TreeSHAP) come with every prediction. Scoring takes 0.024 ms per vehicle.
- **Versioned artefact** with a checksum: [ml/models/failure-7d-v1](ml/models/failure-7d-v1).

**Limits:** simulated data only; lead times are compressed (median 50 h, below the 72 h
target); money is not computed, because costs are still placeholders; the planner switches
to the model once live scoring exists (M9). Read the
[model card](docs/ml/model-card.md) and [ADR-007](docs/architecture/adr/ADR-007.md) before
quoting any number.

## 13. API and live scoring (M9)

- **`prognos-api` (FastAPI).**
  - **Login:** RS256 JWTs published as JWKS, argon2id password hashes, and a
    brute-force guard.
  - **Access control:** RBAC from the schema's role policy, with strict tenant isolation
    (another tenant's data is `404`). Location is masked for analysts.
  - **Endpoints:** keyset-paginated vehicles, alerts and work orders; a work-order state
    machine; a live alert WebSocket; radar signals.
  - **Operations:** RFC 9457 errors, rate limiting, security headers, Prometheus
    metrics, and an audit trail for every change and every denial.
  - **Tests:** 18 integration tests against real PostgreSQL and Redis.
  - Guide: [docs/api](docs/api/README.md). Contract:
    [openapi.json](docs/api/openapi.json), checked by a test.
- **`prognos-scorer`.**
  - **Pipeline:** ClickHouse computes minute buckets, the training feature SQL runs in
    DuckDB, then LightGBM scores and explains the riskier vehicles.
  - **Outputs:** a Redis ranking and ClickHouse history.
  - **Parity:** a test proves ClickHouse and the training pipeline give identical
    features and predictions.
  - **Speed:** 100,000 vehicles per cycle in about 17 s on 4 vCPU
    ([evidence](evidence/benchmarks/m9-scorer-live.json)).
- **Shadow mode, and why.**
  - The first live run put 8% of the fleet above 0.5. The causes were a
    reporting-rate-dependent feature and slopes fitted to a few minutes of data.
  - Fixes: v3 uses rate-invariant features (verified on a held-out run where vehicles
    report every 10 s: PR-AUC 0.679 vs 0.478), and a data-coverage gate only scores
    vehicles with enough data.
  - The planner and the default ranking stay on the calibrated rules until the model is
    validated on live outcomes ([ADR-008](docs/architecture/adr/ADR-008.md)).
- **Bugs found by tests on the way:**
  - A `DateTime64` insert stored scores as 1970, and the TTL silently deleted them.
  - A non-IP client address crashed the audit insert.
  - The brute-force limiter counted successful logins.

## 14. Repository structure

```
apps/        api · simulator · stream-processor · batch · web
packages/    shared schemas (canonical event), common utilities, config
ml/          datasets, features, training, evaluation, model artefacts
database/    postgres (3NF) · telemetry (ClickHouse) · redis key conventions
kafka/       topic declarations (topics.conf) and init script
infra/       docker overlays · monitoring · kubernetes · helm · terraform
tests/       unit · integration · contract · acceptance · load · soak · chaos · security
docs/        product · architecture (+ADRs) · database · algorithms · security · performance
evidence/    measured results only: benchmarks, coverage, security, load tests, chaos
```

## Milestones

| M | Scope | Status |
|---|---|---|
| M0 | Problem selection, MVP, architecture | ✅ Done |
| M1 | Repo bootstrap, Docker Compose, CI, lint/format | ✅ Done |
| M2 | PostgreSQL 3NF + ClickHouse schema, migrations, 100K-vehicle seed | ✅ Done ([data model](docs/database/data-model.md), [ER diagram](docs/database/er-diagram.md)) |
| M3 | Vehicle simulator + standalone benchmark | ✅ Done ([results](docs/algorithms/algorithms.md#measured-results-simulator)) |
| M4 | Kafka ingestion: normalise 3 OEM formats, validate, dedup, DLQ | ✅ Done ([benchmarks](docs/performance/benchmarks.md), [ADR-005](docs/architecture/adr/ADR-005.md)) |
| M5 | Real-time detection: critical rules, trend early-warnings, alerts | ✅ Done ([detection baseline](docs/performance/detection-baseline.md)) |
| M6 | Persistence: ClickHouse Kafka ingestion, PostgreSQL alerts, Redis live state, reconciliation | ✅ Done ([ADR-002..004](docs/architecture/adr/)) |
| M7 | Core intelligence: calibrated risk, capacity-aware work orders, emerging-fault radar | ✅ Done ([A8–A10](docs/algorithms/algorithms.md), [ADR-006](docs/architecture/adr/ADR-006.md)) |
| M8 | ML failure model vs the calibrated-rules baseline (held-out) | ✅ Done ([model card](docs/ml/model-card.md), [ADR-007](docs/architecture/adr/ADR-007.md)) |
| M9 | API (FastAPI, auth, RBAC, WebSocket) + live model scoring | ✅ Done ([API guide](docs/api/README.md), [ADR-008](docs/architecture/adr/ADR-008.md)) |
| M10 | Dashboard (React): fleet map, at-risk list, alerts, work orders, live feed | ⏭ Next |
| M11–M19 | See the M0 document | Planned |

## Declarations

- **Data:** all data is synthetic, generated by this repository's simulator. No real personal or
  vehicle-owner data.
- **AI tools:** Claude Code (Anthropic) is used as a coding assistant for design, code, tests and
  documentation. All results are verified by running them.
- **Affiliation:** Motorq is used only as an industry reference. This is an independent academic
  project with no affiliation to Motorq.
- **Licence:** MIT ([LICENSE](LICENSE)).

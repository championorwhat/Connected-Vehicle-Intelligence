# Prognos: Predictive Maintenance for Connected Vehicle Fleets

> Know which vehicle fails next, and what it will cost if you wait.

Built for the **Connected Vehicle Intelligence Hackathon** by **Pratibimb Gupta**
(RA2311003010027).

**Status:** M5 (real-time detection and alerts) complete. See [milestones](#milestones).
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
make test-integration  # 26 tests against real PostgreSQL 17 + ClickHouse 25.8 (Testcontainers)
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

## 10. Repository structure

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
| M6 | Persistence: ClickHouse, PostgreSQL alerts, Redis live state | ⏭ Next |
| M4–M19 | See the M0 document | Planned |

## Declarations

- **Data:** all data is synthetic, generated by this repository's simulator. No real personal or
  vehicle-owner data.
- **AI tools:** Claude Code (Anthropic) is used as a coding assistant for design, code, tests and
  documentation. All results are verified by running them.
- **Affiliation:** Motorq is used only as an industry reference. This is an independent academic
  project with no affiliation to Motorq.
- **Licence:** MIT ([LICENSE](LICENSE)).

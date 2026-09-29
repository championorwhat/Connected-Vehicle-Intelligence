# M0 — Problem Selection, Product Definition and Plan

| Field | Value |
|---|---|
| Author | Pratibimb Gupta |
| Registration Number | RA2311003010027 |
| Date | 20/09/2026 |
| Status | **Proposed — awaiting confirmation before M1** |
| Sources of truth | *Connected Vehicle Intelligence Hackathon — Problem Statement & Case Study* (Talenciaglobal / SRM), *Solution Document Template* |

> **Rule for this document:** no numbers here are measured results. Every performance, ML or
> business-impact figure is either a **target** (from the brief or set by us) or marked
> **NOT YET MEASURED** / **NOT YET SOURCED**.

---

## 1. Analysis of the eight official problem spaces

| # | Problem space | Who it helps | Engineering strength | Engineering weakness |
|---|---|---|---|---|
| 1 | **Predictive maintenance** | Fleet managers | Every 1 Hz signal matters (coolant, voltage, tyre pressure, DTCs), so 100K ev/s is load the product actually needs. The real-time path (critical faults < 5 s) and batch path (7-day failure model trained on history) are both core to it. Because *our* simulator injects the failures, we own **ground-truth labels**, so precision, recall and lead time can be measured honestly. | Common hackathon topic, so we have to stand out through depth. Needs a physically plausible degradation model in the simulator. A 7-day horizon needs time acceleration for a 5-minute demo. |
| 2 | EV charging & battery health | EV fleets, OEMs | Richest algorithms (Dijkstra/A* to chargers, DP over time-of-use tariffs), SoH forecasting. | Only the EV subset matters, so 100K ev/s is mostly incidental. It needs a synthetic charger network, tariffs and a battery electro-thermal model, which is a lot of simulation surface. Charging decisions are minutes-to-hours, so the real-time case is weaker. |
| 3 | Driver safety scoring | Fleets, insurers | Very strong real-time story (harsh braking, speeding) and a clean demo. | No ground truth for "risky driver", so any "model accuracy" would be circular. Heavy driver-PII concerns. The most common hackathon idea, so differentiation is low. |
| 4 | Asset recovery | Lenders | Geofencing and anomaly detection. Strong location privacy/security story. | Only a small fraction of vehicles are "in recovery" at a time, so batch analytics are thin and ML is weak. |
| 5 | Fuel, idling & utilisation cost | Fleet finance | Easy synthetic data. Very clear $ impact. Great SQL/aggregation story. | Mostly batch arithmetic with shallow real-time and ML. It is literally what Motorq Fuse (Apr 2026) markets, so it reads like a copy. |
| 6 | Multi-OEM normalisation | Platform teams | Best distributed-systems story (schema evolution, zero-downtime onboarding, adapters). | Weak end-user value and weak business-impact numbers. The demo is "data looks the same", which is hard to make compelling in 5 minutes. |
| 7 | Privacy-safe data sharing | OEMs, regulators | Most differentiated (k-anonymity, differential privacy, consent). Strong on compliance. | Weak real-time relevance and no natural ML. Hard to show a business outcome. Risky to implement DP correctly under time pressure. |
| 8 | Fleet carbon reporting | Sustainability | Clean batch analytics and emission-factor maths. | Almost no real-time need (reports are monthly), so it fails the "within seconds" spirit of the brief. |

**Original problem considered:** *fleet-wide emerging-defect detection for OEM quality teams*,
meaning a DTC suddenly spiking across one model or firmware version. On its own it is not stronger
than #1 because it lacks a per-vehicle action. It is **folded into #1 as an innovation** (Count-Min
Sketch heavy hitters, §7).

## 2. Decision matrix (1 = poor, 5 = excellent; for "complexity", 5 = *least* complex)

| Criterion | PM | EV | DS | AR | FIC | MON | PRV | CO2 |
|---|---|---|---|---|---|---|---|---|
| Demonstrate 100K ev/s meaningfully | 5 | 4 | 5 | 3 | 5 | 4 | 3 | 4 |
| Real-time processing relevance | 4 | 3 | 5 | 5 | 3 | 3 | 2 | 1 |
| Batch analytics relevance | 5 | 4 | 4 | 2 | 5 | 2 | 3 | 5 |
| Algorithmic depth | 4 | 5 | 3 | 4 | 3 | 3 | 4 | 2 |
| Data engineering depth | 5 | 4 | 4 | 3 | 4 | 5 | 4 | 4 |
| Distributed systems depth | 4 | 3 | 4 | 4 | 3 | 5 | 3 | 2 |
| ML potential (real, not decorative) | 5 | 4 | 3 | 3 | 3 | 2 | 1 | 2 |
| API / UI potential | 5 | 4 | 4 | 4 | 5 | 2 | 3 | 4 |
| Security / privacy depth | 3 | 3 | 5 | 5 | 3 | 3 | 5 | 2 |
| Ease of realistic synthetic data | 4 | 3 | 4 | 4 | 5 | 5 | 4 | 5 |
| Measurable business impact | 5 | 4 | 3 | 4 | 5 | 2 | 2 | 3 |
| Failure-recovery demonstration | 4 | 3 | 4 | 4 | 3 | 5 | 3 | 2 |
| SQL optimisation showcase | 4 | 3 | 4 | 3 | 5 | 2 | 3 | 4 |
| CAP / PACELC decisions | 4 | 3 | 3 | 4 | 3 | 4 | 3 | 3 |
| Meaningful polyglot storage | 5 | 4 | 4 | 4 | 4 | 3 | 3 | 3 |
| 5-minute demo quality | 4 | 4 | 5 | 5 | 4 | 3 | 2 | 3 |
| Implementation complexity (5 = low) | 3 | 2 | 4 | 4 | 4 | 3 | 2 | 4 |
| Hackathon feasibility | 4 | 3 | 4 | 4 | 5 | 4 | 3 | 4 |
| Differentiation | 3 | 4 | 2 | 3 | 2 | 3 | 5 | 3 |
| Evidence strength for the Solution Document | 5 | 3 | 3 | 3 | 4 | 3 | 2 | 3 |
| **Total (/100)** | **85** | 70 | 77 | 75 | 78 | 66 | 60 | 63 |

Key: PM = Predictive maintenance · EV = EV charging/battery · DS = Driver safety · AR = Asset recovery ·
FIC = Fuel/idling/cost · MON = Multi-OEM normalisation · PRV = Privacy-safe sharing · CO2 = Carbon.

**Sensitivity check:** PM stays first if any single criterion is removed. Its lead over the runner-up
(FIC, +7) is mainly **ML potential (+2)**, **real-time relevance (+1)** and **evidence strength (+1)**,
and those are the areas the brief weights heavily ("testing is heavily weighted", "a model that turns
data into a decision, evaluated against a baseline"). PM's weakest scores are **differentiation (3)**
and **complexity (3)**. §7 addresses differentiation and the MoSCoW cut in §8 addresses complexity.

## 3. Selection: Predictive Maintenance (with two supporting capabilities absorbed)

**Selected:** Predictive maintenance for mixed ICE/EV commercial fleets.

**Absorbed as supporting capabilities (not separate products):**
- **Multi-OEM normalisation.** Required anyway, because the brief's industry model is device-free
  ingestion from OEM clouds in different formats. Implemented as the Adapter layer of the pipeline.
- **Cost impact.** Every prediction carries an *expected cost avoided*, turning a risk score into a
  finance-grade decision.

### Why it maps to the official judging requirements

| Brief requirement | How predictive maintenance exercises it naturally |
|---|---|
| 100K vehicles @ 1 ev/s, 3× burst, no loss | Health signals are needed at full rate. Dropping events hides faults, so "no data loss" is a product requirement, not just an NFR. |
| Real-time within seconds | Critical faults (overheating, rapid tyre deflation, misfire storm) must alert in < 5 s. |
| Batch over billions of rows | Model training needs months of per-vehicle history. Daily fleet-health summaries. |
| Relational + NoSQL + (vector) | Work orders/alerts/ownership need ACID (PostgreSQL). Telemetry needs a columnar time-series store. Latest state and dedup windows need a KV store (Redis). |
| ML vs baseline | The rule baseline ("alert when a DTC appears or a threshold is crossed") vs a learned 7-day failure model, scored against **simulator ground truth**. |
| Algorithms | Sliding-window dedup, streaming trend and change detection, Count-Min Sketch, VIN check digit and DTC parsing, geohash clustering (§7). |
| CAP / PACELC | Telemetry is AP/EL. Work orders and alerts are CP/EC. Latest-state cache is AP/EL. There is a genuine contrast to document. |
| Security & privacy | Tenant isolation between fleet customers. Location masking for roles that don't need it. Retention and erasure. |

## 4. Users and stakeholders

**Primary user (exactly one): Fleet manager**, specifically the *fleet maintenance manager* of a
commercial fleet operator who owns vehicle uptime and the workshop schedule.

**Secondary stakeholders**
| Stakeholder | Interest |
|---|---|
| Workshop technicians | Receive a work order with the suspected component and evidence, not just a DTC |
| Drivers | Fewer roadside breakdowns. Their location data is minimised and masked |
| Fleet finance team | Cost avoided vs inspection cost, and maintenance budget forecast |
| OEM quality / warranty teams | Fleet-wide emerging DTC patterns per model/firmware |
| Platform engineer | Onboarding new OEM payload formats, pipeline health |
| Data protection officer | Retention, erasure, audit trail (GDPR, India DPDP Act 2023 / Rules 2025) |

## 5. Problem statement

> **A fleet maintenance manager running a 100,000-vehicle mixed ICE/EV fleet needs a way to know
> which vehicles are likely to fail in the next 7 days, and why, early enough to book them into a
> workshop, because today faults are discovered as roadside breakdowns or warning lights that nobody
> can triage across tens of thousands of vehicles, which today costs
> *[unplanned breakdowns per 1,000 vehicles per month] × [(unplanned − planned repair cost) + downtime
> cost per day × extra downtime days]*.**

The bracketed impact is a **formula, not a claim**. The parameters are **NOT YET SOURCED**. In M17 we
fill them from cited public sources (industry cost-of-operations reports, published fleet
maintenance studies), each with a confidence rating. In M8 we compute the saving on a held-out
simulated period (back-test). No number will be stated without a source or a measurement.

### Measurable business pain → success metrics

| Metric | Baseline | Target | How measured |
|---|---|---|---|
| Failures caught ≥ 48 h before breakdown (recall) | Rule baseline: NOT YET MEASURED | ML beats baseline by a margin we report honestly | Back-test vs simulator ground truth (M8) |
| Precision of "at-risk" list | NOT YET MEASURED | Report at fixed workshop capacity (top-K/day) | Same back-test |
| Median prediction lead time | NOT YET MEASURED | ≥ 72 h (our target) | Same back-test |
| Expected cost avoided / 1,000 vehicles / month | NOT YET MEASURED | Positive after false-positive inspection cost | Back-test × sourced cost parameters |
| Critical alert latency | NOT YET MEASURED | p95 < 5 s (brief) | Event timestamp → alert persisted/pushed |
| Ingest → dashboard freshness | NOT YET MEASURED | < 2 s (brief) | Event timestamp → WebSocket receipt |
| Sustained ingest | NOT YET MEASURED | 100K ev/s (brief) | Simulator + Kafka + sink metrics |
| Burst | NOT YET MEASURED | 300K ev/s for 5 min, zero loss (brief) | Reconciliation job (produced = stored + DLQ) |
| API latency | NOT YET MEASURED | p95 < 200 ms, p99 < 500 ms (brief) | k6 |

## 6. Product name

| Candidate | Rationale | Concern |
|---|---|---|
| **Prognos** | From *prognostics* (PHM, the engineering discipline of predicting remaining useful life). Short, enterprise, says what it does. | Name is used by unrelated companies in other industries. Fine for an academic project, but check before any commercial use. |
| Axlewise | Automotive, memorable, unique | Doesn't say "prediction" |
| WrenchAhead | Explains itself instantly | Sounds consumer, not enterprise |
| Faultline | Memorable pun | Negative connotation |
| Foreshift | "Foresight" + "shift" (shift-start bursts) | Abstract |

**Recommendation: Prognos.** Tagline: *"Know which vehicle fails next, and what it will cost if you
wait."* The GitHub repository keeps its existing name (`connected-vehicle-intelligence`). Prognos is
the product name inside it.

## 7. Innovations (to address the differentiation score)

1. **Ground-truth-labelled, physics-inspired simulator.** Five failure modes degrade along plausible
   curves (below), and the simulator records the true failure time. This makes ML metrics, lead
   time and $ impact *measurable* instead of asserted. Most telemetry demos cannot evaluate against
   truth.
2. **Cost-aware prioritisation.** The risk list is ranked by `P(failure within 7 d) × (unplanned −
   planned cost)` under a workshop-capacity constraint, not by probability alone. That turns a model
   output into a finance-grade decision.
3. **Fleet-wide emerging-fault radar.** A Count-Min Sketch over (OEM, model, firmware, DTC) in
   sliding windows detects DTCs spiking across a cohort, which is an early recall signal for OEM
   quality teams. It uses sub-linear memory regardless of fleet size.

**Failure modes simulated (mixed ICE/EV):**

| Mode | Leading signals | Fast (critical) signature |
|---|---|---|
| Cooling-system failure (ICE) | Coolant temperature drifting up under similar load | Coolant > critical threshold |
| Ignition misfire (ICE) | Rising RPM variance, intermittent P030x | Misfire DTC storm |
| 12 V battery / starter failure | Declining cranking voltage minima | Crank voltage collapse |
| Tyre slow leak (all) | Negative pressure slope in one tyre | Rapid deflation |
| HV battery thermal / cell imbalance (EV) | Cell ΔV and pack temperature creeping up, SoH decline | Thermal threshold |

## 8. MVP scope (MoSCoW)

### MUST (a credible end-to-end submission is impossible without these)
- **Simulator** built by us. 100K vehicles, 3 OEM payload formats. Asyncio + batching with vehicles
  sharded across worker processes, never one task per vehicle. Configurable rate/burst via env.
  Injects duplicates, out-of-order, delay, malformed, missing fields and the 5 failure modes with
  ground truth. Two modes: **live** (wall-clock 1 Hz stream) and **backfill** (months of history
  generated directly to Parquet for training). Also a **demo scenario trigger** to deterministically
  degrade a chosen vehicle at accelerated sim-time.
- **Kafka** (KRaft, 3 brokers, RF=3, min.insync=2): `telemetry.raw`, `telemetry.canonical`,
  `alerts`, `telemetry.dlq`, keyed by vehicle.
- **Stream processor, normaliser role:** OEM adapters, schema + range validation, VIN check digit,
  DTC parsing, dedup (per-vehicle sequence window), DLQ with reason codes.
- **Stream processor, detector role:** per-vehicle sliding windows and trend features, critical
  rules → alerts (< 5 s target), latest state → Redis, feature snapshots, and periodic ML risk
  scoring.
- **Storage:** PostgreSQL (3NF: tenants, fleets, vehicles, drivers, users/roles, workshops, alerts,
  work orders, audit log). ClickHouse (telemetry + rollups, TTL tiers). Redis (latest state, dedup
  windows, rate limits, pub/sub fan-out). MinIO (S3-compatible) for Parquet warm/cold tier.
- **Batch:** nightly export to Parquet, daily fleet-health summaries, training-dataset builder, and
  a no-data-loss **reconciliation job**.
- **ML:** rule baseline vs a gradient-boosted model (LightGBM) for 7-day failure, time- and
  vehicle-based splits, per-prediction feature contributions (explainability), versioned model
  artefact.
- **Action:** work order creation from an at-risk vehicle, with expected cost avoided.
- **API (FastAPI):** OAuth2/JWT (RS256 + JWKS, so an external OIDC IdP is a config swap), RBAC,
  tenant isolation, keyset pagination, rate limiting, structured errors, `/v1` versioning, audit log,
  WebSocket live feed.
- **Web (React + TS):** Overview (fleet health + $ at risk), At-risk queue, Vehicle detail (signal
  trends + "why"), Live alerts, link to system health.
- **Privacy:** role-based location masking (geohash truncation), retention TTLs, right-to-erasure
  workflow with audit.
- **Observability:** Prometheus metrics on every service, Grafana demo dashboard.
- **Testing:** unit (80%+ on core), integration (Testcontainers), contract, BDD, load (10K/50K/100K/
  300K burst), soak, chaos (kill broker/worker/API), SAST/DAST/dependency/image scans in CI.
- **DevOps:** Docker Compose one-command run, Dockerfiles, Helm chart, Terraform for one cloud,
  GitHub Actions.
- **Docs:** ADRs 001–005, CAP/PACELC, STRIDE, ER diagram, SQL EXPLAIN before/after, algorithms
  write-up, Solution Document, feature traceability, demo script.

### SHOULD
- Count-Min Sketch emerging-fault radar (innovation #3).
- Trip segmentation (engine-on/stop state machine), giving usage features for the model.
- OpenTelemetry traces (collector + Jaeger) across simulator → processor → API.
- HTTP ingest gateway for OEM webhook batches with auth, schema validation and back-pressure (429 +
  Retry-After).
- Keycloak OIDC as an optional compose profile.
- Actual cloud deployment of the Helm chart (only if it can be done within free-tier/credits).

### COULD
- Read-only AI agent ("Why is fleet A's risk up this week?") with typed SQL tools, tenant scoping,
  audit log, a mock-LLM fallback and optional pgvector retrieval over a synthetic DTC knowledge base.
- MQTT (EMQX) direct-device path with mTLS.
- Workshop-slot optimisation (capacity-constrained assignment instead of greedy).
- Schema registry.

### WON'T (explicitly cut)
- Real OEM integrations or any real personal data (brief rule).
- Driver scoring, EV charging optimisation and carbon reporting, which are other problem spaces.
- Spark/Hadoop and a Flink cluster, which are too heavy for a laptop and unnecessary at this scale (ADR-005).
- Multi-region active-active. Billing/payments. Mobile app.
- Any "exactly-once" claim. We implement **at-least-once delivery + idempotent sinks** and say so.

## 9. Core user journey

| Stage | What happens |
|---|---|
| Vehicle | Simulated vehicle (one of 3 OEM formats) emits telemetry at ~1 Hz. A degrading component shifts its signals. |
| Telemetry | OEM-specific JSON payload, with occasional duplicate / late / malformed / missing-field events. |
| Ingestion | Produced to `telemetry.raw` (models OEM-cloud stream delivery), keyed by vehicle, lz4-compressed batches. |
| Validation | Adapter picks the OEM mapping. Schema, range, VIN and DTC checks. Invalid → `telemetry.dlq` with reason. |
| Stream processing | Dedup by sequence window, reorder within allowed lateness, enrich with vehicle metadata, update per-vehicle windows. |
| Detection / prediction | Critical rules fire immediately. Trend features update continuously. The risk model rescores vehicles periodically. |
| Storage | Canonical events → ClickHouse. Alerts/work orders → PostgreSQL. Latest state → Redis. Nightly Parquet → MinIO. |
| API | Tenant-scoped, RBAC-checked REST + WebSocket. |
| Dashboard | Fleet health overview, at-risk queue ranked by $ at risk, vehicle drill-down with explanation. |
| Alert / action | Manager acknowledges the alert and creates a work order at the nearest workshop. The action is audited. |
| Business outcome | Breakdown converted into a planned repair. Cost avoided is tracked per fleet. |

## 10. High-level architecture

```
                    ┌──────────────────────────────────────────┐
                    │ Vehicle Simulator (live | backfill)      │  3 OEM formats, faults,
                    │ N worker procs × asyncio, batched        │  dup/ooo/malformed, burst
                    └───────────────┬──────────────────────────┘
                                    │ Kafka protocol (lz4)       [SHOULD: HTTP ingest gateway]
                                    ▼                            [COULD: MQTT/EMQX + mTLS]
        ┌───────────────────── Kafka (KRaft, 3 brokers, RF=3) ─────────────────────┐
        │ telemetry.raw ─► telemetry.canonical ─► alerts        telemetry.dlq      │
        └───────┬──────────────────────┬──────────────────┬────────────────────────┘
                ▼                      ▼                  │
   ┌────────────────────┐  ┌───────────────────────┐      │
   │ stream-processor   │  │ stream-processor      │      │
   │ role=normalizer    │  │ role=detector         │──────┘ alerts
   │ adapters, validate,│  │ windows, rules, CMS,  │
   │ dedup, DLQ         │  │ ML scoring, state     │
   └────────────────────┘  └───┬─────────┬─────────┘
                               │         │
            ┌──────────────────┘         └──────────────┐
            ▼                                           ▼
   ┌─────────────────┐   ┌──────────────────┐   ┌───────────────┐   ┌──────────────────┐
   │ ClickHouse      │   │ PostgreSQL (3NF) │   │ Redis         │   │ MinIO (S3)       │
   │ telemetry, AP/EL│   │ alerts, WOs,     │   │ latest state, │   │ Parquet warm/cold│
   │ (Kafka engine)  │   │ tenants, audit   │   │ dedup, pubsub │   │ training data    │
   │                 │   │ CP/EC            │   │ AP/EL         │   │                  │
   └────────┬────────┘   └────────┬─────────┘   └───────┬───────┘   └────────┬─────────┘
            │                     │                     │                    │
            │          ┌──────────┴─────────────────────┴──────┐   ┌─────────┴────────┐
            └─────────►│ FastAPI  REST /v1 + WebSocket         │   │ batch (DuckDB),  │
                       │ JWT/RBAC, tenant scope, rate limit,   │   │ ML training      │
                       │ audit, keyset pagination              │   │ (LightGBM)       │
                       └──────────────────┬────────────────────┘   └──────────────────┘
                                          ▼ HTTPS / WSS
                       ┌───────────────────────────────────────┐
                       │ React + TypeScript dashboard          │
                       └───────────────────────────────────────┘
   Cross-cutting: Prometheus · Grafana · OpenTelemetry · Docker · Helm/K8s · Terraform · GH Actions · Trivy/Semgrep/ZAP
```

### Technology decisions (summary; full ADRs in M1+)

| Layer | Choice | Why | Rejected (and why) | CAP / PACELC | Operational cost |
|---|---|---|---|---|---|
| Messaging (ADR-001) | **Kafka (KRaft)** | Durable, partitioned by vehicle (per-vehicle ordering), replay for reprocessing and recovery, consumer groups for horizontal scale | RabbitMQ (no cheap replay/partitioned log). Pulsar (more moving parts). Redis Streams (memory-bound, weaker durability) | CP within ISR (`acks=all`, `min.insync=2`). Trades availability for no-loss on partition | 3 brokers ≈ 3 GB RAM locally. Lite profile with 1 broker |
| Telemetry store (ADR-002) | **ClickHouse** | 100K–300K rows/s inserts with headroom on one node. Columnar compression. Fast time-window aggregates over billions of rows. TTL for tiering. Native Kafka engine and Parquet export | TimescaleDB (simpler since it's the same engine as PG, but less ingest/scan headroom on a laptop, and 300K/s burst is our riskiest NFR). Cassandra (great writes, poor ad-hoc analytics) | AP / EL (async replication, eventual). Acceptable for telemetry | ~1–2 GB RAM |
| Relational (ADR-003) | **PostgreSQL 16** | ACID for work orders, alerts, ownership, RBAC, audit. 3NF. Real EXPLAIN ANALYZE story | MySQL (weaker partial indexes/planner tooling for our needs) | CP / EC (sync commit, single primary) | ~0.5 GB |
| KV / NoSQL | **Redis** | Latest state for 100K vehicles, dedup windows, token-bucket rate limits, pub/sub for WebSocket fan-out | Memcached (no data structures/pubsub) | AP / EL (async replica). Rebuilt from Kafka if lost | ~0.3 GB |
| Object store | **MinIO** (S3 API) | Parquet warm/cold tier and training sets. Same S3 API on any cloud, which keeps us cloud-agnostic | Local disk (not cloud-portable) | n/a (object durability) | ~0.2 GB |
| Stream processing (ADR-005) | **Python consumer groups** (confluent-kafka/librdkafka + orjson), stateful per partition, at-least-once + idempotent sinks | Horizontal scale = partitions × workers. One language across simulator/processor/API/ML. Low footprint | Flink (JVM cluster, heavy locally, Python API friction). Kafka Streams (best technical fit, but Java-only). Faust/Bytewax (maintenance risk) | n/a | **Top technical risk:** Python per-core throughput. **Mitigation:** throughput spike in M4 before committing. Fallback is the hot path in Go, same topics/contracts |
| Batch | **DuckDB / Polars over Parquet** + ClickHouse SQL | Scans hundreds of millions of rows on a laptop, no cluster | Spark (operationally heavy for this scale) | n/a | none extra |
| ML | **scikit-learn baseline + LightGBM** | Tabular, fast inference (µs–ms), built-in feature contributions for explanations | Deep sequence models (need more data than justified, harder to explain) | n/a | none extra |
| API / UI | **FastAPI + React/TS (Vite)** | Async, OpenAPI for free, WebSockets. Typed UI | Spring Boot (second language) | n/a | small |
| Wire format | Raw: **OEM JSON** (realistic). Canonical: **Protobuf**, versioned in `packages/schemas` | JSON reflects OEM reality. Protobuf roughly halves bytes/CPU internally with explicit evolution rules | Avro + registry (extra service; registry is COULD) | n/a | none |
| Observability | Prometheus, Grafana, OTel collector (+ Jaeger) | Brief-listed, open source, local | ELK (heavy RAM) | n/a | ~1 GB |

**Delivery semantics:** at-least-once end to end. Duplicates are absorbed by (a) per-vehicle
sequence-window dedup in the normaliser, (b) `ReplacingMergeTree` keyed by `(vehicle_id, seq)` in
ClickHouse, and (c) idempotent upserts keyed by alert fingerprint in PostgreSQL. The result is
*effectively-once results*, and we will not call it exactly-once.

**Local hardware assumption (to confirm):** Docker Desktop with ≥ 8 vCPU / 16 GB RAM for the full
stack. Whether one laptop can sustain 100K ev/s *end to end* is **NOT YET MEASURED**. We will measure
each stage separately (simulator alone, then Kafka, then processor, then sinks) and report the real
ceiling. If the laptop is the bottleneck, the 100K/300K runs move to the Kubernetes deployment (M16)
and the laptop numbers stay in the document as they are.

## 11. Algorithms (planned; each gets pseudocode, complexity and a benchmark later)

| Problem | Algorithm | Complexity (per event) |
|---|---|---|
| Duplicate & replay detection with out-of-order tolerance | Per-vehicle sliding sequence window (high-watermark + 64/128-bit bitmap, as in IPsec anti-replay). Time-bucketed Bloom filter fallback for OEMs without sequence numbers | O(1) time; O(V·W) bits |
| Late / out-of-order events | Event-time watermark + bounded per-vehicle reorder buffer (min-heap) | O(log B) |
| Degradation trends | EWMA + CUSUM change detection. Online least-squares slope (tyre pressure, cranking voltage) | O(1) time & space per signal |
| Emerging fleet faults | Count-Min Sketch + top-K heap over cohort×DTC, windowed | O(d) update; O(d·w) space |
| VIN / DTC validation | ISO 3779 check digit. SAE J2012 DTC regex | O(1) |
| Map at 100K vehicles | Geohash bucketing for server-side clustering and location masking | O(1) encode |
| Prioritisation | Expected-cost ranking + nearest workshop via geohash neighbours, capacity-aware greedy with a heap | O(n log n) |

## 12. Initial repository structure (baseline from the master prompt, with justified changes)

```
connected-vehicle-intelligence/
├── README.md  LICENSE  .gitignore  .dockerignore  .env.example
├── docker-compose.yml          # profiles: core | full | lite(1 broker) | observability | oidc
├── Makefile  pyproject.toml    # uv/ruff/mypy/pytest config shared by Python apps
├── apps/
│   ├── api/app/{main.py,config,api,domain,application,infrastructure,schemas,security,dependencies}
│   ├── simulator/src/{main.py,generators,scenarios,publishers,faults,schemas,config}
│   ├── stream-processor/src/{main.py,consumers,normalization,deduplication,processors,
│   │                         aggregations,rules,scoring*,state*,sinks*}
│   ├── batch/{jobs,features,analytics,models,tests}
│   └── web/src/{components,pages,hooks,services,stores,types,utils}
├── packages/{schemas,common,config}   # canonical .proto, OEM JSON Schemas, shared utils
├── ml/{data,notebooks,features,training,inference,evaluation,models,tests}
├── database/
│   ├── postgres/{migrations,seeds,schema.sql}
│   ├── telemetry/{schema.sql,migrations}   # ClickHouse DDL
│   └── redis/                              # key-space conventions doc
├── kafka/{topics,schemas,config}
├── tests/{unit,integration,contract,acceptance,load,soak,chaos,security}
├── docs/
│   ├── product/*               # this M0 document, personas, metrics
│   ├── architecture/{context-diagram,container-diagram,deployment,data-flow,cap-pacelc}.md + adr/
│   ├── database/  algorithms/  security/  performance/sql/  testing/  api/  demo/
│   ├── solution-document/solution-document.md
│   └── feature-traceability.csv*
├── infra/{docker,kubernetes,helm,terraform/{modules,environments},monitoring*}
├── scripts/{setup,seed,migrate,benchmark,load-test,chaos-test,security-scan}.sh
├── .github/workflows/{ci,security,performance}.yml
└── evidence/{screenshots,benchmarks,coverage,security,load-tests,chaos,demo}
```
`*` = additions to the baseline:
- `scoring/`, `state/` and `sinks/` separate ML inference, per-partition state and idempotent writers.
- `docs/product/` holds the product decisions.
- `infra/monitoring/` holds Prometheus rules, Grafana dashboards and OTel config.
- `docs/feature-traceability.csv` is required by the master prompt §34.
- `apps/ingest-gateway/` is added **only if** the SHOULD item is built.

## 13. Milestone plan

| M | Goal | Exit criteria (evidence saved to `/evidence`) |
|---|---|---|
| M0 | Problem selection & plan | This document approved |
| M1 | Repo bootstrap | `docker compose config` passes. Lint/format/CI green on an empty skeleton |
| M2 | Data model | PG 3NF schema + ClickHouse DDL + migrations + seed of 100K vehicles/20 tenants. Constraint/index tests pass. ER diagram |
| M3 | Simulator | Standalone benchmark: events/s generated per core at 100K vehicles (JSON/CSV). Fault/dup/ooo/malformed rates verified by tests |
| M4 | Kafka & ingestion (+ **throughput spike**, + basic Prometheus metrics) | Normal, duplicate, malformed and burst traffic verified. DLQ populated with reasons. **Measured per-stage ceiling drives ADR-005** |
| M5 | Stream processing | Normalise, dedup, enrich, windows, rules, alerts. Event→alert latency measured |
| M6 | Persistence | ClickHouse/PG/Redis sinks idempotent. Reconciliation job proves no loss. Retention TTLs |
| M7 | Core intelligence | Health features, rule baseline, cost-aware ranking, work-order action, emerging-fault radar. Back-test harness |
| M8 | ML | Backfill dataset, baseline vs LightGBM, metrics, explanations, inference latency, model version |
| M9 | API | Secure FastAPI (JWT/RBAC/tenant/rate limit/audit/keyset). OpenAPI exported |
| M10 | Dashboard | Live journey end to end: alert appears < 2 s, work order created |
| M11 | Observability | Grafana demo dashboard, OTel traces, alerting rules |
| M12 | Security & privacy | STRIDE doc + controls, masking, erasure workflow, secure headers |
| M13 | Testing | Unit ≥ 80% core, Testcontainers integration, contract, BDD, security scans, chaos |
| M14 | Performance | 10K / 50K / 100K / 300K-burst / soak runs with graphs, then optimisation |
| M15 | SQL optimisation | 3 slowest queries: EXPLAIN ANALYZE before/after, measured |
| M16 | Cloud deployment | Helm + Terraform (one cloud), same images, config-only differences |
| M17 | Documentation | All 17 Solution Document sections, every claim linked to evidence |
| M18 | Demo | 5-minute script rehearsed and recorded |
| M19 | Final audit | Requirement → implementation → file → test → evidence → timestamp table. Tag `v1.0-submission` |

**Order adjustments vs the master prompt (justified):**
1. A throughput spike is pulled into M4 so the riskiest assumption (Python at 100K ev/s) is
   tested before we build on it.
2. Basic Prometheus counters start in M4, because every later measurement depends on them. M11
   finishes observability.
3. JWT/RBAC/tenant isolation land in M9 with the API, not later. M12 hardens and documents them.
4. CI grows every milestone (tests are added as code is added), not only in M13.

## 14. Open items for the author
- Team name and members/roles/emails: **placeholders** until provided.
- Submission date: provided as **20/09/2026**; kept exactly as given.
- Mac hardware (CPU cores / RAM), which decides the local vs cloud load-test plan.
- Confirm product name (Prognos) and the telemetry store (ClickHouse over TimescaleDB).

---

## 15. Amendment (after author feedback): 8 GB MacBook Air M2, solo team

**Decisions confirmed:** Predictive maintenance · product name Prognos · ClickHouse. Team: solo
(Pratibimb Gupta). Submission date: kept as given, to be revisited.

**Hardware reality.** An 8 GB M2 Air gives Docker about 5 GB. That is enough for the whole pipeline
at reduced scale, but not for 100K ev/s end to end with Kafka, ClickHouse, the processors and
the API all on one machine. Claiming otherwise would be fabrication. The scale strategy is:

| Tier | Where | What we prove |
|---|---|---|
| Laptop (daily dev + demo) | Mac, Docker 5 GB, `VEHICLE_COUNT=10000` | Full functionality, the live demo, alert latency at 10K ev/s (target, NOT YET MEASURED) |
| Stage benchmarks | Mac and CI | Simulator alone at 100K vehicles; the normaliser alone; ClickHouse inserts alone. Each stage's own ceiling |
| Scale runs (100K sustained, 300K burst, soak, chaos) | CI runner (`ubuntu-latest`, 4 vCPU / 16 GB) and/or a free-tier cloud VM/K8s (M16) | The brief's NFRs, measured where the hardware allows, with the hardware stated next to every number |

**Changes caused by the 8 GB limit:**
- MinIO dropped from the default stack. MinIO stopped publishing community images in 2025, and
  it would cost RAM. Parquet goes to `OBJECT_STORE_URL` (`file://` locally, `s3://`/`gs://` in
  cloud), which keeps it cloud-agnostic through config alone.
- Observability is an opt-in compose profile.
- 3-broker Kafka is an overlay used for chaos tests, not the default.
- Every container has a memory limit, and a unit test enforces the ≤ 5 GB default budget.

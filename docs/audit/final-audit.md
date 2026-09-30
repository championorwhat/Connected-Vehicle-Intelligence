# Final audit (M19)

Every requirement from the official brief and the Solution Document Template, traced to
what implements it, where, the test that checks it, the evidence, and where the demo
shows it. **Status** is judged against the requirement, not against the plan.

- **Met:** implemented and backed by a test or measured evidence.
- **Partial:** part of it is implemented and the rest is stated.
- **Not met:** not done; the reason is given.

Measurements are from a 4-vCPU x86_64 Linux container unless stated, **not the target
Mac**. Demo times are from the [demo script](../demo/demo-script.md) until the video is
recorded. Every link in this table is checked by `tests/unit/test_documentation.py`.

Audited on 2026-09-30, at the commit that carries this file.

## Summary

| Area | Met | Partial | Not met |
|---|---|---|---|
| Scale and performance | 5 | 2 | 3 |
| Simulator and data | 4 | 0 | 0 |
| Processing, storage, algorithms and ML | 8 | 0 | 0 |
| API, dashboard, security and privacy | 8 | 1 | 0 |
| Observability, failure handling and testing | 6 | 3 | 1 |
| Documentation and submission | 11 | 2 | 1 |
| **Total (55)** | **42** | **8** | **5** |

**Not met:**
1. **100K events/s sustained end to end** (S2): about 15K on this machine.
2. **A true 3× burst for 5 minutes** (S3): the simulator delivered about 22K/s for about
   105 s.
3. **99.9 % availability with no single point of failure** (S8): single-node stores
   locally.
4. **Cloud deployment with Helm, Kubernetes and Terraform** (O9): M16, not built.
5. **Business impact in money** (G12): no sourced repair or downtime costs.

## Scale and performance

| # | Requirement | Implementation | File | Test | Evidence | Demo | Status |
|---|---|---|---|---|---|---|---|
| S1 | At least 100,000 simulated vehicles | Vectorised numpy fleet, sharded across processes | [engine.py](../../apps/simulator/src/prognos_sim/engine.py) | [test_engine.py](../../apps/simulator/tests/test_engine.py) | [m3-simulator-benchmark.json](../../evidence/benchmarks/m3-simulator-benchmark.json) | – | Met |
| S2 | ~100,000 events/s sustained | Stages scale by partition; measured per stage and end to end | [load_test.py](../../scripts/load_test.py) | – | Simulator into Kafka 100K/s ([evidence](../../evidence/benchmarks/m3-simulator-kafka-live-100k.json)); end to end **~15K/s** ([evidence](../../evidence/load-tests/m14-capacity-15000.json), [20K not sustained](../../evidence/load-tests/m14-capacity-20000.json)) | 3:25 (told) | **Not met** |
| S3 | 3× burst for 5 minutes, no data loss | Simulator burst profile; Kafka buffers; reconciliation | [config.py](../../apps/simulator/src/prognos_sim/config.py) | – | 3× requested: ~22K/s delivered for ~105 s, backlog drained in 50 s ([evidence](../../evidence/load-tests/m14-burst-3x.json)); 300K/s into Kafka alone: 173K/s, 0 loss ([evidence](../../evidence/benchmarks/m3-simulator-kafka-burst-300k.json)) | – | **Not met** |
| S4 | No data loss | At-least-once delivery, idempotent writers, reconciliation | [reconcile.py](../../scripts/reconcile.py) | [test_sink_stores.py](../../tests/integration/test_sink_stores.py) | [m6-reconciliation.json](../../evidence/benchmarks/m6-reconciliation.json), [m13-postgres-outage-reconciliation.json](../../evidence/chaos/m13-postgres-outage-reconciliation.json) | 3:25 | Met |
| S5 | Dashboard < 2 s | Detector → Kafka → sink → Redis pub/sub → WebSocket | [live.py](../../apps/api/src/prognos_api/routers/live.py) | [ws_latency.py](../../scripts/ws_latency.py) | p95 1.88 s at 100K vehicles, 10K ev/s ([evidence](../../evidence/load-tests/m17-dashboard-freshness-100k.json)) | 1:15 | Met |
| S6 | Critical alert < 5 s | Streaming rules in the detector | [detector.py](../../apps/stream-processor/src/prognos_stream/detector.py) | [test_detector.py](../../apps/stream-processor/tests/test_detector.py) | p95 1.78 s at 100K vehicles ([evidence](../../evidence/load-tests/m14-100k.json)) | 1:15 | Met |
| S7 | API p95 < 200 ms, p99 < 500 ms | FastAPI, keyset pagination, M15 indexes | [routers](../../apps/api/src/prognos_api/routers/) | [test_login_concurrency.py](../../tests/integration/test_login_concurrency.py) | p95 63–82 ms, p99 127–221 ms, 40 users ([evidence](../../evidence/load-tests/m14-api-40-users-during-100k.json)) | 3:25 (told) | Met |
| S8 | Availability 99.9 %, no single point of failure | Kafka 3-broker overlay; health checks and restarts | [compose.kafka-ha.yml](../../infra/docker/compose.kafka-ha.yml) | – | [m1-kafka-ha-smoke.md](../../evidence/chaos/m1-kafka-ha-smoke.md); PostgreSQL, ClickHouse and Redis are single instances | – | **Not met** |
| S9 | Load tests: baseline, 10K/50K/100K, burst, soak, failure; graphs | Load driver, API load, freshness client | [load-tests.md](../performance/load-tests.md) | [tests/load](../../tests/load/README.md) | [evidence/load-tests](../../evidence/load-tests/), [burst graph](../images/m14-burst.svg), [soak graph](../images/m14-soak.svg) | 3:25 | Partial: rates are 1K/5K/10K ev/s (fleet sizes 10K/50K/100K), not 10K/50K/100K ev/s; no k6/Locust (own scripts) |
| S10 | Measure everything with scripts | Scripts write JSON evidence | [scripts](../../scripts/) | – | [evidence](../../evidence/) | – | Partial: nothing measured on the target Mac |

## Simulator and data

| # | Requirement | Implementation | File | Test | Evidence | Demo | Status |
|---|---|---|---|---|---|---|---|
| D1 | Own simulator: 3 OEM formats, trips, driving patterns, GPS, faults with ground truth | Physics-inspired fleet model; ORION / VEGA / LYRA formats | [fleet.py](../../apps/simulator/src/prognos_sim/fleet.py), [formats.py](../../apps/simulator/src/prognos_sim/formats.py) | [test_fleet.py](../../apps/simulator/tests/test_fleet.py) | [algorithms A1–A4](../algorithms/algorithms.md) | 1:00 | Met |
| D2 | Duplicates, out-of-order, missing fields, malformed, invalid values, network delay, bursts | Fault injection, counted for reconciliation | [engine.py](../../apps/simulator/src/prognos_sim/engine.py) | [test_engine.py](../../apps/simulator/tests/test_engine.py) | [m3-simulator-benchmark.json](../../evidence/benchmarks/m3-simulator-benchmark.json) | – | Met |
| D3 | Configurable through environment variables | `VEHICLE_COUNT`, `EVENTS_PER_SECOND`, `BURST_*`, rates | [.env.example](../../.env.example) | [test_main.py](../../apps/simulator/tests/test_main.py) | – | – | Met |
| D4 | Canonical event schema: validation, versioning, dedup, idempotency, unknown OEMs | JSON Schema v1 with `schema_version`; adapters; DLQ | [canonical-telemetry-v1.schema.json](../../packages/schemas/canonical-telemetry-v1.schema.json) | [test_canonical_schema.py](../../tests/contract/test_canonical_schema.py), [test_oem_contract.py](../../tests/contract/test_oem_contract.py) | [m4-pipeline-reconciliation.json](../../evidence/benchmarks/m4-pipeline-reconciliation.json) | – | Met |

## Processing, storage, algorithms and ML

| # | Requirement | Implementation | File | Test | Evidence | Demo | Status |
|---|---|---|---|---|---|---|---|
| P1 | Real-time validation, dedup, normalisation, enrichment | Normalizer consumer group | [processor.py](../../apps/stream-processor/src/prognos_stream/processor.py), [dedup.py](../../apps/stream-processor/src/prognos_stream/dedup.py) | [test_processor.py](../../apps/stream-processor/tests/test_processor.py), [test_normalizer_kafka.py](../../tests/integration/test_normalizer_kafka.py) | [m4-pipeline-reconciliation.json](../../evidence/benchmarks/m4-pipeline-reconciliation.json) | – | Met |
| P2 | Aggregation, anomaly and risk detection, alerts, latest state; horizontally scalable | Detector (streaming statistics), sink, Redis state | [detector.py](../../apps/stream-processor/src/prognos_stream/detector.py), [sinks.py](../../apps/stream-processor/src/prognos_stream/sinks.py) | [test_detector_kafka.py](../../tests/integration/test_detector_kafka.py) | [m5-detection-backtest.json](../../evidence/benchmarks/m5-detection-backtest.json) | 1:15 | Met |
| P3 | Batch / historical analytics | Parquet datasets, DuckDB features, rollups, back-tests | [apps/batch](../../apps/batch/README.md) | [test_ml.py](../../ml/tests/test_ml.py) | [m8-datasets.json](../../evidence/benchmarks/m8-datasets.json) | – | Met |
| P4 | PostgreSQL in 3NF with keys and constraints; ER diagram | 20 tables; business rules in the schema | [migrations](../../database/postgres/migrations/) | [test_postgres_schema.py](../../tests/integration/test_postgres_schema.py) | [data-model.md](../database/data-model.md), [er-diagram.md](../database/er-diagram.md) | – | Met |
| P5 | Time-series store, Redis only where justified, no vector DB for show | ClickHouse (Kafka engine, TTL tiers); Redis for live state and pub/sub | [telemetry migrations](../../database/telemetry/migrations/) | [test_clickhouse_schema.py](../../tests/integration/test_clickhouse_schema.py) | [m17-clickhouse-storage.json](../../evidence/benchmarks/m17-clickhouse-storage.json), [ADR-002](../architecture/adr/ADR-002.md) | 3:00 | Met |
| P6 | Meaningful algorithms with pseudocode, complexity and measured scale | A1–A10 | [algorithms.md](../algorithms/algorithms.md) | [test_dedup.py](../../apps/stream-processor/tests/test_dedup.py), [test_radar.py](../../apps/stream-processor/tests/test_radar.py), [test_planner.py](../../apps/stream-processor/tests/test_planner.py) | [m7-radar-hot-path.json](../../evidence/benchmarks/m7-radar-hot-path.json), [m7-planner-100k.json](../../evidence/benchmarks/m7-planner-100k.json) | 2:05, 2:35 | Met |
| P7 | ML against a baseline: dataset, split, leakage, metrics, latency, version, explanations | LightGBM vs calibrated rules; TreeSHAP; shadow mode | [model.py](../../ml/src/prognos_ml/model.py) | [test_ml.py](../../ml/tests/test_ml.py), [test_feature_parity.py](../../tests/integration/test_feature_parity.py) | [m8-model-vs-baseline.json](../../evidence/benchmarks/m8-model-vs-baseline.json), [model card](../ml/model-card.md) | 4:15 | Met |
| P8 | SQL: 3 slowest queries, EXPLAIN ANALYZE before and after | Indexes, a query rewrite, an ORDER BY fix | [sql-optimisation.md](../performance/sql-optimisation.md) | [test_query_optimisations.py](../../tests/integration/test_query_optimisations.py) | [m15-summary.json](../../evidence/performance/m15-summary.json), [plans](../performance/sql/README.md) | – | Met (2 of 3 improved; the third is reported as not improved) |

## API, dashboard, security and privacy

| # | Requirement | Implementation | File | Test | Evidence | Demo | Status |
|---|---|---|---|---|---|---|---|
| A1 | REST, OpenAPI, versioning, keyset pagination, filters, structured errors, rate limits | FastAPI `/v1` | [routers](../../apps/api/src/prognos_api/routers/), [openapi.json](../api/openapi.json) | [test_api.py](../../tests/integration/test_api.py), [test_openapi.py](../../apps/api/tests/test_openapi.py) | [API guide](../api/README.md) | – | Met |
| A2 | JWT / OAuth2, RBAC, tenant isolation, audit logs | RS256 + JWKS; server-side role policy; row-level security | [security.py](../../apps/api/src/prognos_api/security.py), [deps.py](../../apps/api/src/prognos_api/deps.py) | [test_access_matrix.py](../../tests/integration/test_access_matrix.py), [test_row_level_security.py](../../tests/integration/test_row_level_security.py), [test_token_attacks.py](../../tests/security/test_token_attacks.py) | [ADR-010](../architecture/adr/ADR-010.md) | 1:00 | Met |
| A3 | React + TypeScript dashboard showing real-time changes | Overview, alerts (WebSocket), vehicle, work orders, signals | [apps/web/src/pages](../../apps/web/src/pages/) | [dashboard.spec.ts](../../apps/web/tests/e2e/dashboard.spec.ts) | [storyboard](../../evidence/demo/storyboard/) | 1:00–3:00 | Met |
| A4 | Useful screens (analytics, cost / impact, system health) | Cost shown only when sourced; system health in Grafana | [Overview.tsx](../../apps/web/src/pages/Overview.tsx) | [app.test.tsx](../../apps/web/tests/unit/app.test.tsx) | [grafana-overview.png](../images/grafana-overview.png) | 3:00 | Met |
| A5 | STRIDE threat model: threat, control, test | Per-category tables with code and tests | [threat-model.md](../security/threat-model.md) | [test_token_attacks.py](../../tests/security/test_token_attacks.py) | [threat-model.md](../security/threat-model.md) | – | Met |
| A6 | TLS, secrets, encryption at rest, least privilege, secure headers, validation, OWASP | Secure headers, non-root images, restricted DB role, `.env`; production refuses defaults | [security-headers.inc](../../apps/web/security-headers.inc) | [test_compose_security.py](../../tests/unit/test_compose_security.py), [test_security.py](../../apps/api/tests/test_security.py) | [m12-nginx-headers.txt](../../evidence/security/m12-nginx-headers.txt) | – | **Partial:** no TLS, Kafka/Redis auth or device identity locally (residual risks R1, R2, R7) |
| A7 | Privacy: minimisation, masking, retention, erasure, audit; GDPR and DPDP discussed | Pseudonymous drivers; ~1 km masking; erasure worker | [erasure.py](../../apps/api/src/prognos_api/erasure.py), [privacy.py](../../apps/api/src/prognos_api/routers/privacy.py) | [test_erasure.py](../../tests/integration/test_erasure.py) | [m12-erasure-live.json](../../evidence/security/m12-erasure-live.json), [privacy.md](../security/privacy.md) | – | Met |
| A8 | AI agent optional; only if valuable | No agent: no use case justified one | – | – | [Solution Document §8](../solution-document/solution-document.md#8-security--compliance) | – | Met (deliberately not built) |
| A9 | Action: work order from an at-risk vehicle | Capacity-aware planner; work-order state machine | [planner.py](../../apps/stream-processor/src/prognos_stream/planner.py), [work_orders.py](../../apps/api/src/prognos_api/routers/work_orders.py) | [test_planner_postgres.py](../../tests/integration/test_planner_postgres.py), [test_acceptance.py](../../tests/integration/test_acceptance.py) | [m18-rehearsal-timeline.json](../../evidence/demo/m18-rehearsal-timeline.json) | 2:05 | Met |

## Observability, failure handling and testing

| # | Requirement | Implementation | File | Test | Evidence | Demo | Status |
|---|---|---|---|---|---|---|---|
| O1 | Prometheus + Grafana dashboard suitable for the demo | Dashboards generated from code; SLO burn-rate alerts | [gen_dashboards.py](../../scripts/gen_dashboards.py), [rules](../../infra/monitoring/prometheus/rules/prognos.rules.yml) | [prognos.test.yml](../../infra/monitoring/prometheus/tests/prognos.test.yml), [test_observability.py](../../tests/unit/test_observability.py) | [grafana-overview.png](../images/grafana-overview.png), [slo.md](../observability/slo.md) | 3:00 | Met |
| O2 | OpenTelemetry | Deferred with reasons; request and event ids in JSON logs | [logs.py](../../packages/common/src/prognos_common/logs.py) | – | [ADR-009](../architecture/adr/ADR-009.md) | – | **Partial:** no tracing |
| O3 | Failure handling: broker, service, database, duplicate, out-of-order, malformed | DLQ, retries with backoff and jitter, idempotency, health checks, graceful shutdown | [sinks.py](../../apps/stream-processor/src/prognos_stream/sinks.py), [kafka_loop.py](../../apps/stream-processor/src/prognos_stream/kafka_loop.py) | [test_sink_main.py](../../apps/stream-processor/tests/test_sink_main.py), [test_sinks.py](../../apps/stream-processor/tests/test_sinks.py) | [drills.md](../observability/drills.md), [m18-recovery-rehearsal.md](../../evidence/demo/m18-recovery-rehearsal.md) | 3:25 | Met |
| O4 | Circuit breakers | Not implemented; the API degrades live views when Redis is down | – | – | [Solution Document §6.3](../solution-document/solution-document.md#63-design-patterns-used) | – | **Partial** |
| O5 | No false exactly-once claims | At-least-once plus idempotent writers, stated as such | [ADR-004](../architecture/adr/ADR-004.md) | [test_sink_stores.py](../../tests/integration/test_sink_stores.py) | [cap-pacelc.md](../architecture/cap-pacelc.md) | – | Met |
| O6 | Unit ≥ 80 % on core; Testcontainers integration; contract; BDD | 233 unit, 80 integration, 5 BDD scenarios; CI coverage gate | [ci.yml](../../.github/workflows/ci.yml) | [features](../../tests/integration/features/fleet_manager.feature) | [m13-coverage.txt](../../evidence/coverage/m13-coverage.txt) (84 %) | – | Met |
| O7 | Security scans: Semgrep, Trivy, OWASP ZAP | Semgrep, Trivy (filesystem and images), gitleaks in CI | [security.yml](../../.github/workflows/security.yml) | – | [trivy-fs.txt](../../evidence/security/trivy-fs.txt), [m13-image-scan.txt](../../evidence/security/m13-image-scan.txt) | – | **Partial:** no ZAP (DAST) |
| O8 | Chaos: kill broker, container, worker; verify recovery | 3 drills with reconciliation | [drills.md](../observability/drills.md) | [tests/chaos](../../tests/chaos/README.md) | [m1-kafka-ha-smoke.md](../../evidence/chaos/m1-kafka-ha-smoke.md), [m11-detector-outage-drill.json](../../evidence/chaos/m11-detector-outage-drill.json), [m13-postgres-outage.json](../../evidence/chaos/m13-postgres-outage.json) | 3:25 | Met (run by hand, not in CI) |
| O9 | Kubernetes, Terraform, Helm | Not built (M16) | [infra/helm](../../infra/helm/README.md) | – | – | – | **Not met** |
| O10 | Data lifecycle: hot, warm, cold; storage estimate | TTL tiers; per-event sizes measured | [data-model.md](../database/data-model.md#6-capacity-estimate) | [test_clickhouse_schema.py](../../tests/integration/test_clickhouse_schema.py) | [m17-clickhouse-storage.json](../../evidence/benchmarks/m17-clickhouse-storage.json) | – | Met (storage cost in money not estimated) |

## Documentation and submission

| # | Requirement | Implementation | File | Test | Evidence | Demo | Status |
|---|---|---|---|---|---|---|---|
| G1 | Solution Document: all 17 template sections | Evidence-linked | [solution-document.md](../solution-document/solution-document.md) | [test_documentation.py](../../tests/unit/test_documentation.py) | – | – | Met |
| G2 | Submission as PDF | Exported with diagrams, `make pdf` | [export_solution_pdf.py](../../scripts/export_solution_pdf.py) | – | [solution-document.pdf](../solution-document/solution-document.pdf) | – | Met (regenerate after filling in the team and video link) |
| G3 | ADRs (messaging, telemetry DB, relational DB, CAP, stream processing) | ADR-001 to ADR-010 | [adr](../architecture/adr/) | – | [cap-pacelc.md](../architecture/cap-pacelc.md) | – | Met |
| G4 | Feature list traceable to code with MoSCoW, status, video time | 60 features | [feature-traceability.csv](../feature-traceability.csv) | [test_documentation.py](../../tests/unit/test_documentation.py) | – | – | Met (video times from the script until recorded) |
| G5 | Demo video ≤ 5 min following the template's segments | Script timed from a rehearsal | [demo-script.md](../demo/demo-script.md) | – | [m18-rehearsal-timeline.json](../../evidence/demo/m18-rehearsal-timeline.json) | all | **Partial:** recording pending (author) |
| G6 | README: problem, architecture, quick start, env vars, tests, known issues | – | [README.md](../../README.md) | [test_documentation.py](../../tests/unit/test_documentation.py) | – | – | Met |
| G7 | One-command run with the simulator | `make pipeline-demo` | [Makefile](../../Makefile), [docker-compose.yml](../../docker-compose.yml) | CI compose job | – | – | Met |
| G8 | CI: build, lint, all suites, security scans on every push | 8 jobs | [ci.yml](../../.github/workflows/ci.yml), [security.yml](../../.github/workflows/security.yml) | – | – | – | Met |
| G9 | Hygiene: no secrets, `.env.example`, commits from all members | gitleaks; solo author | [.env.example](../../.env.example) | [security.yml](../../.github/workflows/security.yml) | [gitleaks.txt](../../evidence/security/gitleaks.txt) | – | Met |
| G10 | Declarations: open source with licences, AI tools, synthetic data | SBOMs and a generated licence list | [open-source.md](../open-source.md) | – | [evidence/sbom](../../evidence/sbom/README.md) | – | Met |
| G11 | Team details in the header | – | [solution-document.md](../solution-document/solution-document.md) | – | – | – | **Partial:** team name and email are placeholders |
| G12 | Business impact from sourced costs | Formula only; money shown as "not sourced" | [planner.py](../../apps/stream-processor/src/prognos_stream/planner.py) | [test_postgres_schema.py](../../tests/integration/test_postgres_schema.py) (costs must cite a source) | [Solution Document §2.2](../solution-document/solution-document.md#22-evidence--validation) | 2:05 | **Not met:** no sourced costs |
| G13 | Final tag `v1.0-submission` | Tagged on `main` after this audit merged | – | – | – | – | Met |
| G14 | Measured numbers only | Every number links to evidence or says NOT YET MEASURED | [evidence](../../evidence/) | [test_documentation.py](../../tests/unit/test_documentation.py) | – | – | Met |

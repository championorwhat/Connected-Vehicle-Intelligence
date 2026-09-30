# Testing strategy (M13)

Every layer runs in CI on every push (`.github/workflows/ci.yml`, `security.yml`). The
numbers below are counted from the repository at M13.

| Layer | What it proves | Where | Count | Runs on |
|---|---|---|---|---|
| **Unit** | Algorithms and rules in isolation: detector rules and trends, planner scheduling, radar statistics, adapters, calibration, features, security primitives | `apps/*/tests`, `ml/tests`, `packages/*/tests`, `tests/unit` | 231 (with security and contract) | no Docker, ~1.5 min |
| **Contract** | The canonical event schema and each OEM payload format stay compatible | `tests/contract` | included above | no Docker |
| **Security** | 14 forged-token attacks; compose hardening | `tests/security`, `tests/unit/test_compose_security.py` | included above | no Docker |
| **Integration** | Real PostgreSQL, ClickHouse, Kafka and Redis (Testcontainers): schema constraints, RLS, access matrix, erasure, sinks, planner, API, query rewrites | `tests/integration` | 78 | Docker, ~3 min |
| **Acceptance (BDD)** | User journeys in Gherkin: alert → proposal → scheduled repair, idempotent planning, tenant isolation, role limits, location masking | `tests/integration/features/*.feature` + `test_acceptance.py` | 5 scenarios | with integration |
| **Web** | Components (Vitest) and full journeys in a browser against the nginx container (Playwright), including security headers | `apps/web/tests` | 7 + 4 | CI `web` and `compose` jobs |
| **System smoke** | The whole compose stack: 100K-vehicle seed, pipeline, reconciliation, planner, scorer, API, dashboard | CI `compose` job | 1 run | ~5 min |
| **Monitoring rules** | Every alert fires on a synthetic failure and stays quiet on normal traffic | `infra/monitoring/prometheus/tests` | promtool | CI `compose` job |
| **Chaos** | Detector loss (M11), PostgreSQL loss mid-ingestion (M13), each with reconciliation | [drills](../observability/drills.md) | 2 drills | manual, recorded |
| **Scans** | Secrets (gitleaks), code (Semgrep), dependencies and Dockerfiles (Trivy, gating), built images (Trivy, gating on fixable CRITICAL) | `security.yml`, `ci.yml` | 4 scanners | every push |

The BDD scenarios live next to the integration tests because they need the same real
databases and fixtures (`tests/integration/conftest.py`).

## Coverage

CI measures **unit + integration combined**, branch coverage, over all five Python
packages. The `coverage` job fails below 80 %. Locally: `make coverage`.

- **Measured: 84 %** (4,532 statements) ([report](../../evidence/coverage/m13-coverage.txt)).
- Core logic is 94–99 %: `detector.py`, `planner.py`, `radar.py`, `processor.py`,
  simulator `engine.py` and `fleet.py`.
- The lowest files are process entry points: `*_main.py` argument parsing, signal and
  metrics wiring, and the `bench.py` CLI. The compose smoke job runs them, but they are
  not measured there.
- This coverage **replaced an earlier configuration that silently measured only 3 of the
  5 packages**. The API and ML code were not counted at all before M13.

## Checking that the tests can fail

A test that cannot fail proves nothing. During M12–M15 several tests were checked by
breaking the code on purpose:
- With location masking disabled, the analyst BDD scenario fails.
- The old nginx config was served and measured: 0 of the 4 headers the e2e test requires.
- Each `promtool` alert test fires on its synthetic failure.

## Known gaps
- **Load and soak** are measured scripts, not CI jobs (`scripts/load_test.py`,
  `scripts/api_load.py`, `scripts/ws_latency.py`): 10K/50K/100K vehicles, burst and a
  30-minute soak on a 4-vCPU Linux container ([load tests](../performance/load-tests.md)).
  On the target Mac they are **NOT YET MEASURED**.
- Chaos drills are run by hand and recorded, not in CI: stopping services on shared CI
  runners is slow and flaky.
- The container image scan passes the CRITICAL gate. Base-image OS packages carry fixable
  **HIGH** findings (OpenSSL in Debian 13.7; curl, OpenSSL and c-ares in Alpine 3.23.4;
  [report](../../evidence/security/m13-image-scan.txt)). These are fixed by rebuilding on
  refreshed base images, which the Dependabot patch group proposes.

# Threat model (STRIDE)

Scope: the local Docker Compose deployment and the code in this repository, as of M12.
Every control links to where it is implemented and the test that checks it. Anything
without a test is marked, and the residual risks are listed at the end.

## Assets

| Asset | Why it matters | Where |
|---|---|---|
| Vehicle location (live and history) | Personal data: it can reveal where a driver lives and goes | ClickHouse `events` (30 days), Redis `veh:{id}` and `tenant:{t}:geo`, Kafka (1 day) |
| Fleet data per tenant (vehicles, alerts, work orders, costs) | Commercially confidential between competing fleets | PostgreSQL |
| Driver ↔ vehicle link | Turns telemetry into data about a person | PostgreSQL `driver_assignments` (drivers are pseudonyms only) |
| Credentials and signing key | Take over any account or mint tokens | `users.password_hash` (argon2id), `JWT_PRIVATE_KEY_FILE` |
| Audit trail | Evidence of who did what | PostgreSQL `audit_log` (append-only) |
| Alert integrity | A fake or suppressed critical alert affects safety decisions | Kafka `alerts`, PostgreSQL, Redis pub/sub |

## Trust boundaries

```
 Browser ──HTTPS*──► nginx (web) ──► API ──► PostgreSQL (RLS: tenant role)
                                      │──► Redis (tenant-keyed reads)
                                      └──► ClickHouse (fleet_signals only)
 Devices / simulator ──► Kafka ──► normalizer ──► detector ──► sink ──► PostgreSQL / Redis
                                                               └──► ClickHouse (Kafka engine)
 DPO ──► API (erasure request) ··· erasure worker ──► PostgreSQL + ClickHouse + Redis
```
\*TLS terminates at the load balancer in the cloud deployment (M16). Locally, everything is
plain HTTP on 127.0.0.1.

The boundaries that matter are:
1. Internet → web/API: untrusted users.
2. Devices → Kafka: untrusted payloads.
3. Tenant A ↔ tenant B inside shared stores.
4. Host network ↔ containers.

## STRIDE

### Spoofing (pretending to be someone else)
| Threat | Control | Code | Test |
|---|---|---|---|
| Forged or altered access token (alg=none, HS256 with the public key, edited claims, other audience or issuer, expired) | RS256 only, `algorithms=["RS256"]`, required claims, `aud`/`iss` checked, 15-minute lifetime | `apps/api/src/prognos_api/security.py` | `tests/security/test_token_attacks.py` (14 cases) |
| Password guessing | argon2id; 5 failed attempts per minute per account *and* per address; unknown and wrong-password look identical (same text, same timing via a dummy hash) | `security.py`, `routers/auth.py` | `tests/integration/test_api.py` (rate limit, identical errors) |
| Token stolen from a URL or log | WebSocket token sent as the first message, never in the URL; tokens never logged | `routers/live.py` | `test_api.py` (WebSocket) |
| Fake telemetry from a device | Payload schema validation and a registered-VIN check (unknown vehicles go to the DLQ) | `apps/stream-processor/.../canonical.py`, `normalizer` | normalizer tests. **Residual R1:** no per-device identity |

### Tampering (changing data)
| Threat | Control | Code | Test |
|---|---|---|---|
| SQL injection | Parameterised queries everywhere; the few f-strings interpolate only fixed column lists or table names; Semgrep in CI | routers | `security.yml` (Semgrep `p/python`) |
| Editing or deleting audit history | Trigger rejects UPDATE/DELETE on `audit_log`; the tenant role has no DELETE or TRUNCATE and can only append its own tenant's rows | core schema, `20261002000004_row_level_security.sql` | `test_postgres_schema.py`, `test_row_level_security.py` |
| Writing into another tenant | RLS `WITH CHECK`; composite `(id, tenant_id)` foreign keys | ADR-010 | `test_row_level_security.py`, `test_api.py` |
| Invalid state changes (e.g. completing a cancelled work order) | Explicit state machine plus database CHECK constraints | `routers/work_orders.py` | `test_api.py` (lifecycle) |

### Repudiation (denying an action)
| Threat | Control | Code | Test |
|---|---|---|---|
| "I never acknowledged that alert" | Every write and every access denial is audited with actor, tenant, request id and client IP; request ids also appear in JSON logs | `audit.py`, `deps.require` | `test_api.py` (audit rows), `test_erasure.py` |
| Erasure done but not provable | Filing and execution are both audited; the request keeps what was erased and what remains (residual window) | `erasure.py`, `routers/privacy.py` | `test_erasure.py` |

### Information disclosure
| Threat | Control | Code | Test |
|---|---|---|---|
| Tenant A reads tenant B (a missed filter) | App filter **and** PostgreSQL RLS (ADR-010); other tenants' ids return 404, never 403 | `deps.tenant_db` | `test_row_level_security.py`, `test_api.py` |
| Analyst sees precise location | Positions rounded to about 1 km without `location:read_precise` | `routers/vehicles.py` | `test_api.py` (masking) |
| Fleet sizes of competitors leak through the radar | Signals are cross-tenant; absolute counts are withheld, only rates are returned | `routers/live.py` | By construction (the query selects no count columns); no test |
| Password hashes readable via the API role | Column grants exclude `password_hash` | RLS migration | `test_row_level_security.py` |
| Databases reachable from the LAN (Redis and Kafka have no auth locally) | **Fixed in M12:** every published port binds to `HOST_BIND` (127.0.0.1 by default) | `docker-compose.yml` | `tests/unit/test_compose_security.py` |
| Clickjacking, MIME sniffing, XSS impact | CSP (`frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP on every response. **Fixed in M12:** nginx had silently dropped these on every page ([evidence](../../evidence/security/m12-nginx-headers.txt)) | `apps/web/security-headers.inc`, `main.SECURITY_HEADERS` | `test_access_matrix.py`, `dashboard.spec.ts` |
| Stack traces or internals in errors | RFC 9457 problem responses with a request id only | `errors.py` | `test_api.py` |
| Secrets in the repository | `.env` ignored; gitleaks on every push; production refuses development defaults | `config.validate` | `security.yml`, `test_security.py` ([evidence](../../evidence/security/gitleaks.txt)) |

### Denial of service
| Threat | Control | Code | Test |
|---|---|---|---|
| API flooding by a signed-in user | Per-user rate limit (Redis, fixed window); page size at most 200; body limit of 64 KB at nginx | `ratelimit.py`, `nginx.conf` | `test_api.py` |
| Telemetry burst | Kafka buffers; consumers scale out; burst of 300K events measured in M3/M4 | ADR-005 | `evidence/benchmarks/` |
| One container starving the others | Memory limit on every service; Redis `noeviction` fails loudly | `docker-compose.yml` | M1 memory evidence |
| Poison message blocks a partition | DLQ with a reason; the sink isolates bad rows with savepoints | normalizer, sink | normalizer and sink tests |

### Elevation of privilege
| Threat | Control | Code | Test |
|---|---|---|---|
| A role calls an endpoint it should not | Permissions come from the server-side `role_permissions` table, never from token claims; `require()` on every route | `deps.require`, `security.principal_from_claims` | `test_access_matrix.py` checks all 5 roles × 17 endpoints against the policy and fails if a new route has no entry |
| Platform admin reads fleet data | `platform_admin` has no fleet permissions and no tenant, so RLS returns nothing | RBAC migration | `test_access_matrix.py`, `test_row_level_security.py` |
| Container escape to the host | Non-root users in all images; no privileged containers or host namespaces | Dockerfiles | Trivy misconfiguration scan, `test_compose_security.py` |
| Vulnerable dependency | Locked dependencies; Trivy gate (HIGH/CRITICAL fails CI); Dependabot weekly | `uv.lock`, `package-lock.json`, `.trivyignore` | `security.yml` ([baseline](../../evidence/security/trivy-fs.txt): 0 findings) |

## Residual risks

| # | Risk | Severity (local / production) | Why it is accepted now | Plan |
|---|---|---|---|---|
| R1 | Devices publish to Kafka without their own identity; anyone who can reach Kafka and knows a VIN can inject telemetry | Low / **High** | Kafka listens only on 127.0.0.1 and the Docker network | Ingest gateway with per-device mTLS certificates (M16); Kafka SASL/TLS |
| R2 | Redis and Kafka have no authentication or TLS | Low / **High** | Same: local binding only | Managed Redis with AUTH and TLS, and Kafka SASL_SSL (M16) |
| R3 | A deactivated user's token keeps working until it expires (at most 15 minutes) | Medium | A short lifetime limits the window; no revocation list is kept | Check `is_active` per request, or keep a Redis denylist keyed by `jti` |
| R4 | The owner database role bypasses RLS; a bug in pipeline code is not caught by it | Medium | Pipeline code keys every write by tenant and composite foreign keys | Separate login roles per service (M16) |
| R5 | The rate limiter fails open if Redis is down | Low | It protects capacity; authorisation does not depend on it | Also rate-limit at the load balancer (M16) |
| R6 | After an erasure, raw payloads remain in Kafka (1 day), the DLQ (7 days) and the compacted `vehicle.state` topic (until the vehicle's next snapshot) | Medium | Retention removes them; the request records this window | Kafka tombstone for `vehicle.state` from the worker; shorter DLQ retention |
| R7 | No TLS locally | Low | Loopback only | TLS at the cloud load balancer (M16) |
| R8 | Container images (OS packages) are not scanned, only manifests and Dockerfiles | Medium | Base images are pinned to maintained tags | `trivy image` on built images in CI (M13) |

## How to check
```bash
uv run pytest tests/security tests/unit/test_compose_security.py        # no Docker
uv run pytest tests/integration/test_row_level_security.py \
              tests/integration/test_access_matrix.py tests/integration/test_erasure.py
./scripts/security-scan.sh                                             # Trivy gate
```

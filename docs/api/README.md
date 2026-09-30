# Prognos API (v1)

**Base URL:** `http://localhost:8000` (`make pipeline`; interactive docs at `/docs`).
**Contract:** [openapi.json](openapi.json), checked in and kept current by a test.
**Streaming contract:** [asyncapi.yaml](asyncapi.yaml).

## Authentication

```bash
make users   # demo users per role in the first tenant (password: DEMO_USER_PASSWORD)
TOKEN=$(curl -s -X POST localhost:8000/v1/auth/token \
  -d username=fleet_manager@demo.prognos.local -d password=change-me-local-only | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" localhost:8000/v1/auth/me
```

**Tokens**
- OAuth2 password grant. Returns a 15-minute access token (JWT, RS256).
- The public key is at `/.well-known/jwks.json`, so an external OIDC provider can replace
  the issuer later without changing verification.
- Tokens carry roles only. Permissions come from the server's role policy
  (`role_permissions`), so a forged `permissions` claim is ignored.

**Passwords and login limits**
- Passwords are hashed with argon2id.
- A wrong password and an unknown email give the same answer, in the same time.
- After 10 failed logins per address and account per minute, the API answers `429`.
  Successful logins never count toward the limit.

**Production mode.** With `APP_ENV=production`, the API refuses to start without a
configured signing key, with default passwords, or with wildcard CORS.

## Authorisation and tenancy

| Role | Can |
|---|---|
| fleet_manager | read fleet, vehicles, alerts, precise location; acknowledge alerts; create/schedule/cancel work orders |
| technician | read vehicles, alerts, work orders; start and complete work orders |
| analyst | read fleet, vehicles, alerts; location is **masked to ~1 km** |
| dpo | read vehicles and the audit trail |
| platform_admin | tenant administration only, **no fleet data** |

- **Tenant isolation.** Every query is filtered by the caller's tenant.
  - Another tenant's resource answers `404`, never `403`, so its existence is not
    revealed.
  - Composite foreign keys make a cross-tenant work order impossible even at the
    database level.
- **Audit.** Denied requests are recorded as `access.deny`. Logins and every
  state-changing action are also written to the append-only `audit_log`.

## Endpoints

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/v1/auth/token` | none | form: `username`, `password` |
| GET | `/v1/auth/me` | any token | roles and effective permissions |
| GET | `/v1/vehicles` | vehicle:read | keyset pages; filter `status`, `model_code`; live state from Redis |
| GET | `/v1/vehicles/at-risk` | vehicle:read | `?source=rules` (default) or `model` (shadow, ADR-008); reports `fallback` |
| GET | `/v1/vehicles/{id}` | vehicle:read | includes open alert count, live state, model risk with reasons |
| GET | `/v1/alerts` | alert:read | newest first; filter `status`, `severity`, `vehicle_id`, `failure_mode` |
| POST | `/v1/alerts/{id}/acknowledge` | alert:ack | idempotent; `409` if already resolved |
| GET | `/v1/work-orders` | work_order:read | filter `status`, `vehicle_id` |
| POST | `/v1/work-orders` | work_order:write | `409` if an active order exists for the vehicle and failure mode |
| POST | `/v1/work-orders/{id}/schedule\|cancel` | work_order:write | state machine enforced (`409` otherwise) |
| POST | `/v1/work-orders/{id}/start\|complete` | work_order:complete | `complete` requires an `outcome` |
| GET | `/v1/fleet/signals` | fleet:read | radar signals for your models; rates only, no other fleets' counts |
| WS | `/v1/ws/alerts` | alert:read | send `{"token": "..."}` first; then this tenant's alert transitions |
| GET | `/healthz`, `/readyz`, `/metrics` | none | liveness, readiness (PostgreSQL required), Prometheus |

## Conventions

- **Pagination** is keyset-based. Pass `next_cursor` back as `cursor`; `limit` goes up
  to 200. Pages stay stable while new rows arrive, and the cost does not grow with the
  page number.
- **Errors** follow RFC 9457 `application/problem+json`, with `type`, `title`,
  `status`, `detail` and `request_id` (echoed in `X-Request-ID`). Validation errors
  list each field.
- **Rate limit:** 600 requests per minute per user. Excess requests get `429` with a
  `Retry-After` header.
- **Security headers** on every response: `nosniff`, `DENY` framing,
  `no-referrer`, `no-store`, and a restrictive CSP.

## Measured

- **Tests.** 18 integration tests run against real PostgreSQL and Redis
  (`tests/integration/test_api.py`), covering:
  - login, token forgery (`alg: none`, a foreign key, expired tokens) and brute-force
    limits;
  - tenant isolation across complete pagination walks;
  - location masking;
  - the alert and work-order state machines, with their audit trail;
  - the WebSocket feed.
- **Latency under load:** **NOT YET MEASURED** (load tests are planned for M14).

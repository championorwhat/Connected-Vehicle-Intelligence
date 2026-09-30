# Privacy: what personal data Prognos holds, for how long, and how it is erased

Prognos is built for data minimisation. It stores no driver names, phone numbers or
licence numbers. Drivers exist only as tenant-issued pseudonyms (`DRV-123456`). The
personal data it does hold is **location**, together with the **link between a
pseudonym and a vehicle**, which ties location to a person.

This is an engineering description, not legal advice. It is written so that a data
protection officer can see what the system does.

## Data inventory

| Data | Personal? | Store | Retention | Who can see it |
|---|---|---|---|---|
| Raw telemetry, including GPS | Yes (location) | Kafka `telemetry.raw` / `.canonical` | 1 day (`KAFKA_TELEMETRY_RETENTION_MS`) | Pipeline services only |
| Quarantined payloads | Possibly | Kafka `telemetry.dlq`, ClickHouse `dlq_events` | 7 days | Operators |
| Event history, including GPS | Yes | ClickHouse `events` | 30 days (TTL) | Pipeline, model training |
| Per-minute aggregates (speed, temperatures, DTCs; **no location**) | No | ClickHouse `vehicle_minute` | 400 days | Pipeline, model |
| Live state and last position | Yes | Redis `veh:{id}` (1 h TTL), `tenant:{t}:geo` | Until overwritten or erased | API, by role |
| Latest health snapshot, including GPS | Yes | Kafka `vehicle.state` (compacted) | Latest record per vehicle | Pipeline |
| Driver pseudonym ↔ vehicle, over time | Yes (linkable) | PostgreSQL `driver_assignments` | Until erased | Tenant (not exposed by the API) |
| Alerts, work orders, costs | No (vehicle maintenance) | PostgreSQL | Policy: 2 years (purge **not automated yet**) | Tenant, by role |
| User accounts | Yes (email) | PostgreSQL `users` | While the account exists | Platform; password hashes never leave the owner role |
| Audit trail | Yes (actor, IP) | PostgreSQL `audit_log` (monthly partitions) | Policy: drop partitions older than 2 years (**not automated yet**) | DPO, platform admin |

## Controls

- **Masking.** Only roles with `location:read_precise` (fleet manager) see exact positions.
  Analysts get positions rounded to 2 decimals (about 1 km) and a
  `location_precision: "approx_1km"` field (`routers/vehicles.py`).
- **Tenant isolation.** Application filters plus PostgreSQL row-level security
  ([ADR-010](../architecture/adr/ADR-010.md)).
- **Model training** uses per-minute aggregates and DTC rates, not location
  (`ml/src/prognos_ml/features.py`).
- **Tenant setting.** Each tenant has a `location_retention_days` setting (default 30),
  which matches the ClickHouse TTL. **Not enforced per tenant yet:** a tenant that asks
  for less than 30 days still gets 30. The erasure worker is the place to add this.

## Right to erasure

A data protection officer (`dpo` role, permission `privacy:erase`) files a request. The
erasure worker carries it out. Every step is audited.

```
POST /v1/privacy/erasure-requests  {"subject_type": "vehicle" | "driver", "subject_id": "…"}
  → 202 {"request_id": "…", "status": "received"}   (200 with the same request if one is open)
GET  /v1/privacy/erasure-requests/{id}
  → status: received → in_progress → completed | rejected
```

The worker (`prognos-api erasure-worker`, the compose service `erasure-worker`, polling
every 60 s) works as follows:

| Subject | What is erased | What is kept, and why |
|---|---|---|
| **Driver** | All `driver_assignments` rows for the pseudonym. No telemetry can then be tied to the person. `drivers.erased_at` is set. | The pseudonym row (no content), so audit entries still resolve |
| **Vehicle** | Location history: `events.latitude/longitude` set to NaN in ClickHouse (the mutation is awaited). Redis live state and last position deleted. Driver links removed. | The vehicle, alerts, work orders and sensor readings: maintenance records, not location |

- **Idempotent and safe to retry.** If ClickHouse or Redis is down, the PostgreSQL part
  rolls back and the request returns to `received`, with `attempts` and `last_error`
  recorded. After 5 failed attempts it is marked `rejected` for manual follow-up.
- **Residual window, recorded in every completed request.** Raw Kafka topics keep payloads
  until retention (telemetry 1 day, DLQ 7 days). The compacted `vehicle.state` topic keeps
  the vehicle's latest snapshot until its next one. So erasure is complete in the
  queryable stores at once, and in every store within 7 days.
- **Tests:** `tests/integration/test_erasure.py` covers access (DPO only, own tenant),
  vehicle and driver erasure checked in PostgreSQL, ClickHouse and Redis, and the outage
  and retry path.

## Not yet done
- Automated purge for the 2-year PostgreSQL policies (alerts, work orders, audit
  partitions). The monthly audit partitions make this a cheap `DROP TABLE`.
- Per-tenant location retention shorter than 30 days (see above).
- Kafka tombstones for `vehicle.state` on vehicle erasure (threat model R6).
- Subject access export (a copy of a subject's data). Drivers are pseudonymous, so the
  tenant, which holds the pseudonym mapping, is the controller who answers this.

# CAP / PACELC decisions

The decision record is [ADR-004](adr/ADR-004.md). In short:

- **CP (and EC):** Kafka writes (`acks=all`, min ISR) and PostgreSQL (alerts, work orders,
  tenants, audit).
- **AP (and EL):** ClickHouse telemetry history, Redis live state, in-process detector state.
  All of these are rebuildable or deduplicated by deterministic keys.
- **Glue:** at-least-once delivery plus idempotent sinks, proven by
  `scripts/reconcile.py` (`evidence/benchmarks/m6-reconciliation.json`) and run in CI after
  every pipeline smoke test.

# M1 — Kafka HA smoke test (preliminary chaos evidence)

Measured: 2026-09-29T05:12:35Z · Environment: Linux container, 4 vCPU / 15 GB (not the target Mac)
Stack: `docker compose -f docker-compose.yml -f infra/docker/compose.kafka-ha.yml` (3 KRaft brokers)

| Step | Result |
|---|---|
| telemetry.raw created | 6 partitions, RF=3, min.insync.replicas=2, ISR = all 3 brokers |
| Kill broker kafka-2 (`docker kill`) | — |
| Produce 1,000 messages with `acks=all` while broker down | **1,000 / 1,000 written** (offset sum = 1000) |
| Under-replicated partitions while down | 6 (expected: every partition lost one replica) |
| Restart kafka-2, wait 20 s | **0 under-replicated partitions** (self-healed) |

Scope: a smoke test of the replication config, not the full M13 chaos test
(which will kill a broker under 100K ev/s load and reconcile end to end).

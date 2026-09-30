# Benchmarks

Every figure links to a JSON evidence file produced by a script in this repository. Unless
stated otherwise, the hardware is a Linux container with 4 vCPU / 15 GB (x86_64). That is **not**
the target 8 GB MacBook Air, and all services share the same 4 vCPUs.

| Stage | Metric | Result | Evidence |
|---|---|---|---|
| Seed | 100,000 vehicles into PostgreSQL | ~18 s | `evidence/benchmarks/m2-seed-100k.json` |
| Simulator (no Kafka) | 100K vehicles, 1 / 2 / 4 workers | 100,140 / 192,924 / 321,527 ev/s | `evidence/benchmarks/m3-simulator-benchmark.json` |
| Simulator → Kafka | live 100K ev/s, 60 s | target held, 0 s lag, 6.06 M messages, 100% reconciled | `evidence/benchmarks/m3-simulator-kafka-live-100k.json` |
| Simulator → Kafka | 3× burst (300K ev/s), 30 s | **not sustained**: 173K ev/s, 0 loss | `evidence/benchmarks/m3-simulator-kafka-burst-300k.json` |
| Normalizer | 1 process, from Kafka | 25,779 msg/s | `evidence/benchmarks/m4-normalizer-1-process.json` |
| Normalizer | 3 processes, one consumer group | 62,889 msg/s | `evidence/benchmarks/m4-normalizer-3-process-*.json` |
| Pipeline | raw → canonical + DLQ + duplicates | balances exactly; 0 duplicate event_ids; lag 0 | `evidence/benchmarks/m4-pipeline-reconciliation.json` |
| Detector | back-test recall / precision / median lead | 98.6% / 100% (simulated world) / 49.6 h | `evidence/benchmarks/m5-detection-backtest.json` |
| Detector | critical alert latency, live (p50 / p95 / p99) | **0.495 / 1.105 / 3.592 s** (target < 5 s) | `evidence/benchmarks/m5-alert-latency-live.json` |
| Pipeline | ingest latency, on-time events (p50 / p95 / p99) | **0.35 / 1.04 / 1.52 s** (includes the simulator's injected 0.3 s mean network delay) | `evidence/benchmarks/m4-pipeline-ingest-latency.json` |

## How to reproduce
```zsh
make sim-bench                                    # simulator alone
make up && make seed && make pipeline             # full pipeline (same VEHICLE_COUNT for both)
docker compose logs -f normalizer                 # watch
make dlq-peek                                     # inspect rejects with reasons
```

## Known gaps (NOT YET MEASURED)
- 100K events/s end to end (every vehicle reporting every second). M14 measured the
  capacity of this 4-vCPU machine at about 15K events/s, with everything sharing the CPUs
  ([load tests](load-tests.md)).
- A true 3× burst end to end: the simulator delivered about 22K/s when asked for 30K/s
  (M14).
- Any measurement on the target MacBook Air.

## M7: planner and radar (4 vCPU Linux container, not the target Mac)

| What | Result | Evidence |
|---|---|---|
| Planner cycle, 100K vehicles, 5,000 open alerts (synthetic backlog) | 0.90 s: 4,204 proposed, 796 unscheduled (no capacity in 7 days), 2,792 booked after the predicted failure | [m7-planner-100k.json](../../evidence/benchmarks/m7-planner-100k.json) |
| Planner re-run on the same state | 28 ms, 0 new proposals (idempotent) | same |
| Batched inserts vs a round trip per row | 3.5 s → 0.9 s (same cycle, measured before and after) | same |
| Radar hot path | 695 ns/event (about 1.4 M events/s), Kafka excluded | [m7-radar-hot-path.json](../../evidence/benchmarks/m7-radar-hot-path.json) |
| Radar live, 100K vehicles at 10K ev/s | Defect cohort flagged; 0 other signals | [m7-radar-live.json](../../evidence/benchmarks/m7-radar-live.json) |

The backlog of 5,000 alerts at once (5% of the fleet) is a stress case: it exceeds what
the seeded workshops can absorb before the short cooling deadlines, so many proposals are
flagged late. The planner reports this rather than hiding it.


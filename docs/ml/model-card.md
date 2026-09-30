# Model card: failure-7d (v1 → v3)

> **Current version: failure-7d-v3 (M9)**, served by `prognos-scorer` in **shadow mode**
> ([ADR-008](../architecture/adr/ADR-008.md)).
>
> | | v1 (M8) | v3 (M9) |
> |---|---|---|
> | DTC inputs | counts | per-event rates (reporting-rate independent) |
> | Event count as input | yes | no (used only by the data gate) |
> | Tyre ratio | all engines, implicit NULL handling | only when all four pressures are present |
> | Held-out PR-AUC (rules: 0.508) | 0.742 | 0.739 |
> | Held-out run, vehicles reporting every 10 s (rules: 0.478) | not tested | **0.679**, gain +0.20 [0.15, 0.25] |
>
> - **Parity.** Serving computes features in ClickHouse and DuckDB with the training SQL.
>   A parity test proves the buckets, features and predictions are equal.
> - **Data gate.** A vehicle is scored only with at least 30 events covering at least
>   45 minutes of the 60-minute window. Without the gate, 6 minutes of live data put 8%
>   of the fleet above 0.5 ([evidence](../../evidence/benchmarks/m9-scorer-live.json)).
> - **Live distribution over full windows:** NOT YET MEASURED.
> - v3 evidence: [m9-model-v3-vs-baseline.json](../../evidence/benchmarks/m9-model-v3-vs-baseline.json).
>   The rest of this card describes v1, whose data, method and limits v3 shares.

## failure-7d-v1 (M8)

**What it predicts:** the probability that a vehicle has a breakdown (any failure mode) within
the next **7 days** (168 h real time), from its recent telemetry.
**Type:** LightGBM gradient-boosted trees, binary objective, 94 trees.
**Artefact:** [`ml/models/failure-7d-v1/`](../../ml/models/failure-7d-v1/) (`model.txt`, 330 KB,
plus `metadata.json` with the feature list, category codes, parameters and SHA-256). Loading
refuses a file whose checksum or feature list does not match.
**Evidence:** [m8-model-vs-baseline.json](../../evidence/benchmarks/m8-model-vs-baseline.json),
[m8-datasets.json](../../evidence/benchmarks/m8-datasets.json).
**Reproduce:** `make ml-data` (about 15 min, 5 runs in parallel), then `make ml-train` (about 15 s).

## Data

All data is **simulated**; no real vehicle data is used. Each dataset is one 8-hour simulated
run of 300 vehicles (about 8.5 M canonical events). It passes through the production
normalizer and detector in one process, so the model sees the same canonical events as the
live system, including duplicates, late events and dropped fields.

| Run | Seed | Degradation speed (time scale) | Fault rate | Failures | Used for |
|---|---|---|---|---|---|
| train_a | 42 | 48× | 0.2 | 138 | training |
| train_b | 43 | 48× | 0.2 | 131 | training |
| test_heldout | 7 | 48× | 0.2 | 120 | **main test**: unseen vehicles and faults |
| test_slow | 11 | 32× | 0.2 | 102 | shift: faults develop 1.5× slower |
| test_rare | 13 | 48× | 0.05 | 29 | shift: 4× fewer faults (lower base rate) |

Train and test never share a seed, so the vehicles, faults and noise in test are unseen.
During training, early stopping uses a validation set of 20% of the training *vehicles*,
so no vehicle appears on both sides.

## Features (DuckDB over Parquet, [features.py](../../ml/src/prognos_ml/features.py))

- Snapshots are taken every 5 simulated minutes, on minute boundaries.
- Features describe the preceding 60-minute window (level, extreme, least-squares slope)
  and 10-minute window (current level). Sources:
  - coolant level, and its residual against speed;
  - spread of engine rpm around its expected value;
  - resting and minimum 12 V voltage;
  - tyre pressure min/max ratio;
  - HV cell imbalance, pack temperature and state of health;
  - DTC counts per failure mode;
  - vehicle model and powertrain.
- **No look-ahead:** a test recomputes window counts from the raw events and requires an
  exact match. It caught a real bug, now fixed: snapshots were not aligned to minutes, so a
  window could include up to 59 s of the future.
- **Excluded on purpose:** odometer and model year. The simulator's fault hazard does not
  depend on mileage or age, so in this data they only identify vehicles, which is a
  memorisation risk. The first version ranked odometer second in importance, which is how
  this was spotted. In a real fleet they are candidate features to test.

**Label.** 1 if a ground-truth FAILURE of any mode happens in (t, t + 7 days]. Rows while the
vehicle is broken down, and the last 7 days of each run (outcome not yet observable), are
dropped.

## Baseline

The baseline is **what the planner does today (M7):** the maximum calibrated rule probability
among the vehicle's open alerts. Vehicles with no open alert get the training-set failure rate
for "no alert" rows (0.094), so the baseline is a fair, calibrated competitor. Both scorers
are evaluated on exactly the same rows.

## Results (held-out; 4-vCPU Linux container, not the target Mac)

"Precision at capacity" means the workshop can take the top 5% of the fleet at every
snapshot. "Recall ≥ 48 h" is the share of failures whose vehicle made that list at least
48 h before breaking down.

| Test set | Scorer | PR-AUC | ROC-AUC | Brier ↓ | Precision at capacity | Recall ≥ 48 h | Median lead |
|---|---|---|---|---|---|---|---|
| **held-out** | **model** | **0.742** | **0.830** | **0.060** | **99.7%** | **34.7%** | 50.4 h |
| held-out | rules (M7) | 0.508 | 0.749 | 0.083 | 72.5% | 23.2% | 38.1 h |
| slower faults | model | 0.785 | 0.831 | 0.085 | 100% | 23.5% | 46.1 h |
| slower faults | rules | 0.645 | 0.777 | 0.095 | 100% | 15.3% | 45.0 h |
| rarer faults | model | 0.702 | 0.858 | 0.018 | 49.2% | 62.5% | 53.2 h |
| rarer faults | rules | 0.550 | 0.817 | 0.024 | 46.0% | 50.0% | 52.6 h |

**PR-AUC gain, model minus rules** (95% bootstrap interval, resampling whole vehicles):

| Test set | Gain | 95% interval |
|---|---|---|
| held-out | +0.230 | [0.171, 0.302] |
| slower faults | +0.139 | [0.101, 0.180] |
| rarer faults | +0.147 | [0.057, 0.236] |

The intervals exclude zero on all three test sets.

**Explanations.** Every prediction has TreeSHAP contributions. For the five highest-risk
held-out vehicles (all were 12 V battery failures), the top reasons are recent DTCs, then
low minimum voltage, then low resting voltage.

**Most important features (gain):** recent DTC count 35%, minimum 12 V voltage 11%, tyre
pressure ratio 8%, peak coolant 6%.

**Inference cost:** 0.024 ms per vehicle (p50; 0.074 ms p99), and about 0.48 M vehicles/s in
batch on 2 threads. Re-scoring 100K vehicles takes about 0.2 s.

## Limits (read before quoting any number)

1. **Simulated world.** The faults, their signatures and the noise all come from our own
   simulator, so the model may be learning the simulator. Real-fleet performance is **NOT
   YET MEASURED**.
2. **Time compression.** Degradation runs 32 to 48× faster than real time, but driving does
   not. A 60-minute feature window covers about 48 h of degradation but only 1 hour of
   driving. Absolute lead times are therefore conservative, and the M0 target of a median
   lead of at least 72 h is **not met** (50 h).
3. **Recall at ≥ 48 h is low for both scorers** (35% vs 23%), because most faults become
   visible late in their degradation. The model improves on the rules; it does not solve
   early detection.
4. **Money.** Expected cost avoided is **NOT COMPUTED**, because repair and downtime costs
   are still placeholders (M7, ADR-006).
5. **Shadow only.** Since M9 the model scores the live fleet (v3, same feature code as
   training, proven by a parity test), but the planner still ranks with the calibrated
   rules until the model is validated on live outcomes. See ADR-007 and ADR-008.

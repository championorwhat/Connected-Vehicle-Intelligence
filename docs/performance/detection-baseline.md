# Detection baseline (M5): rules + streaming trends, scored against ground truth

This is the **rule-based baseline** that the ML model (M8) must beat. All numbers come from
evidence files produced by scripts in this repository.

## 1. Back-test (offline, deterministic)

`make backtest` runs the real simulator → normaliser → detector code in one process on a
simulated clock. It then scores every alert against the simulator's ground truth
(`sim.truth`: fault onset and failure times).

Setup: 300 vehicles, 8 simulated hours, 8,726,351 raw messages, fault rate 0.2, degradation compressed 48×
(lead times below are converted back to real-time hours). Evidence:
[`m5-detection-backtest.json`](../../evidence/benchmarks/m5-detection-backtest.json).

| Metric | Result |
|---|---|
| Failures (breakdowns) in the run | 138 |
| Warned before breakdown (recall) | **98.6%** (136 / 138) |
| Precision (alerts inside a real fault episode of that mode) | **100%** of 566 alerts |
| Median lead time before breakdown | **49.6 h** (min 5.4 h) |

| Failure mode | Failures | Warned | Recall | Median lead | Min lead |
|---|---|---|---|---|---|
| COOLING_FAILURE | 29 | 29 | 100% | 42.8 h | 9.7 h |
| IGNITION_MISFIRE | 17 | 17 | 100% | 91.4 h | 19.0 h |
| LV_BATTERY_FAILURE | 29 | 27 | 93% | 83.2 h | 6.0 h |
| TYRE_SLOW_LEAK | 51 | 51 | 100% | 28.8 h | 5.4 h |
| HV_BATTERY_THERMAL | 12 | 12 | 100% | 151.5 h | 92.9 h |

### How to read these numbers (important)
- **Precision of 100% is a property of the simulator, not a field result.** The detector
  thresholds were designed knowing the simulator's degradation physics. The simulator has no
  nuisance variability: no heat waves, sensor glitches, mis-set tyre pressures or towing loads.
  Real-world precision would be lower. We do not claim it.
- **12 V battery recall is the weakest (93%).** Resting voltage is only observable while a
  vehicle is parked. With degradation compressed 48×, a fault can run its course between two
  parking periods. This is a genuine limitation of that signal at high compression.
- **What M8 must show:** an ML model evaluated on *held-out simulator variants* the rules were
  not tuned on (different noise levels, degradation shapes and driving mixes), compared against
  this baseline on the same data.

## 2. Live latency (Kafka, end to end)

Simulator → Kafka → normaliser (2 replicas) → Kafka → detector, all on one 4-vCPU container.
Latency = detector time − the triggering event's device timestamp. It therefore includes the
simulator's injected network delay (mean 0.3 s) and deliberate 1–30 s out-of-order holds.
Evidence: [`m5-alert-latency-live.json`](../../evidence/benchmarks/m5-alert-latency-live.json).

| Alerts | n | p50 | p95 | p99 |
|---|---|---|---|---|
| Critical | 344 | **0.495 s** | **1.105 s** | 3.592 s |
| Warning | 339 | 0.474 s | 1.181 s | 1.631 s |

Target (brief): critical alert < 5 s. **Met at p99** in this run. The maximum (29.996 s) is an event the
simulator deliberately held back 30 s before delivery.

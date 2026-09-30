# Algorithms and Data Structures

Each entry: problem → algorithm → why → pseudocode → complexity → scale tested → measured
result. Measurements state the hardware they ran on. Entries are added as milestones land.

---

## A1. Vectorised fleet physics (simulator)

**Problem.** Advance 100,000 vehicles (position, speed, trip state, thermal and electrical
state, degradation) every tick without one Python object or task per vehicle.

**Algorithm.** Structure-of-arrays: every attribute is a numpy array of length *n*; one tick
is ~40 whole-array operations (masks, `np.where`, `np.clip`). Trip/park is a two-state machine
with exponentially distributed durations; speed tracks a target within acceleration limits;
heading is a random walk that steers home outside a pattern-specific radius.

```
step(t, dt):
    handle_breakdowns_and_repairs(t)
    start new faults with probability hazard*dt           # Bernoulli per healthy vehicle
    mode_remaining -= dt; toggle driving where <= 0
    target <- retarget / stop / restart (random masks)
    speed  <- clip(speed + clip(target - speed, -brake*dt, accel*dt), 0, 130)
    heading <- heading + N(0, σ√dt); steer home where distance > radius
    lat, lon, odometer <- dead reckoning from speed and heading
```

**Why.** Numpy moves the per-vehicle loop into C; the only Python-level loop left is JSON
encoding of the events actually emitted.

**Complexity.** O(n) per tick time, O(n) memory (~25 arrays).

**Measured.** See A2 (the physics cost is included in the end-to-end generation rate).

## A2. Rate-exact round-robin emission with fractional carry

**Problem.** Emit exactly `rate × multiplier` events per second, spread evenly so each vehicle
reports at the same cadence, across burst changes and without drift.

**Algorithm.** Per tick, `wanted = rate·mult·dt + carry`, emit `k = ⌊wanted⌋` events for the
next *k* vehicles of a circular cursor, keep `carry = wanted − k`.

**Complexity.** O(k) per tick; O(1) extra state. Exactness is tested: 10 s at 1,000 ev/s yields
exactly 10,000 events; a 5 s 3× burst yields exactly 5,000 + 15,000
(`apps/simulator/tests/test_engine.py`).

## A3. Delayed delivery with a min-heap (out-of-order + late duplicates)

**Problem.** Deliver a sampled 2% of events 1–30 s late (and half of duplicates 1–10 s late)
so downstream sees genuine out-of-order arrival.

**Algorithm.** Push `(release_time, tiebreak, key, value, headers)` into a binary min-heap; each
tick pop while `heap[0].release_time ≤ now`. The tiebreak counter keeps ordering stable and
avoids comparing payloads.

**Complexity.** O(log h) per push/pop, h = held-back events (~rate × 2% × 15 s ≈ 30K at
100K ev/s). On shutdown every held-back event is released before the producer flushes.

## A4. Degradation model with ground truth

**Problem.** Produce faults whose *leading indicators* appear before failure, and record the true
onset and failure times, so a predictive model can be evaluated honestly (M8).

**Algorithm.** Per faulty vehicle, progress `p = clip((t − t0)/(tf − t0), 0, 1)`; signals shift by
mode-specific convex functions (coolant +32·p², rpm noise σ 180·p², tyre −38%·p, cell ΔV
+140·p² mV, 12 V −1.4·p^1.5 at rest). DTCs appear stochastically as p grows. At `t ≥ tf` the
vehicle breaks down (ground-truth `FAILURE`), is immobile for 8–48 h, then repaired.
New onsets follow a per-vehicle hazard chosen so the steady-state fraction of degrading
vehicles ≈ `FAULT_RATE`.

**Complexity.** O(n) per tick (vectorised); ground-truth records O(faults).

**Tested.** Each mode's leading signal moves in the expected direction; misfire RPM variance
more than doubles; failure → breakdown → repair cycle (`apps/simulator/tests/test_fleet.py`).

## A5. VIN check digit and DTC parsing

See `packages/common/src/prognos_common/vin.py` (ISO 3779 transliteration, weights, mod 11;
O(1)) and `dtc.py` (SAE J2012 regex; O(len) extraction from free text).

## A6. Exact duplicate detection with a per-vehicle sliding sequence window (normalizer)

**Problem.** Drop duplicate deliveries (1% injected, some arriving up to 10 s late) without
dropping genuine out-of-order events (2% arriving 1–30 s late). A Bloom filter would give false
positives, which here means silently lost events.

**Algorithm.** IPsec-style anti-replay window: per vehicle, the highest sequence number seen plus
a W-bit bitmap (W = 1024) of which of the W numbers below it have been seen.

```
check(v, seq):
    if no state: state[v] = (seq, 1); return NEW
    high, bits = state[v]
    if seq > high:  bits = (bits << (seq-high) | 1) & mask; high = seq; return NEW
    d = high - seq
    if d >= W: return TOO_OLD            # forwarded, flagged late; ClickHouse is the backstop
    if bits >> d & 1: return DUPLICATE
    bits |= 1 << d; return NEW_LATE      # forwarded, flagged late
```

**Complexity.** O(1) time per event (shift/mask on a 1024-bit Python int); O(V·W/8) memory:
~13 MB for 100K vehicles.

**Measured.** Over 1,190,234 raw messages (including a simulator restart), **0 duplicate
event_ids** reached `telemetry.canonical`, and 23,000+ out-of-order events were forwarded and
flagged `late` (`evidence/benchmarks/m4-pipeline-*.json`). Unit semantics:
`apps/stream-processor/tests/test_dedup.py`.

## A7. Streaming change and trend detection with hysteresis (detector)

**Problem.** Warn about each of the five failure modes early (hours to days before breakdown)
and raise critical alerts within seconds. The state must be O(1) per vehicle, because 100K
vehicles each emit 1 event/s.

**Algorithms** (`apps/stream-processor/src/prognos_stream/features.py`, `detector.py`):

| Signal | Statistic | Update | Open / clear |
|---|---|---|---|
| Coolant drift | Page's CUSUM of `coolant − (88 + 0.03·speed)` with k = 4 °C, only after 15 min of ignition | `s ← max(0, s + x − k)` | s > 60 / s < 5 |
| Misfire roughness | EW mean and variance (West) of `rpm − (750 + 28·speed)`, α = 0.02 | `m += α·d; v = (1−α)(v + d·α·d)` | σ > 90 / σ < 70 rpm |
| 12 V battery | EWMA of resting voltage (ignition off), α = 0.05 | `m += α(x − m)` | < 12.25 V / > 12.45 V (critical < 11.8) |
| Tyre slow leak | per tyre: deficit vs the **median of the other three** (cancels temperature and load), EWMA plus exponentially time-weighted least-squares slope → leak rate and hours to critical | weighted sums decayed by `exp(−Δt/τ)` | > 8% / < 5% (critical > 25%) |
| HV cell imbalance | EWMA of cell ΔV | as above | > 40 mV / < 25 mV (critical > 100) |
| Critical / warning DTCs | per-vehicle 10-min deque of codes; critical codes fire at once, warning codes need ≥ 3 in the window | amortised O(1) | present / absent for 10 min |

**Hysteresis.** Separate open and clear thresholds give one alert per episode rather than a
storm near the threshold. **Idempotency:** fingerprint = BLAKE2b(vehicle, rule, seq of the
opening event), so a replay reproduces the same alerts (tested against real Kafka).
**Out-of-order events** update no trend state, because streaming statistics assume time order.
Immediate threshold rules still see them.

**Complexity.** O(1) time per event per signal. **Measured** state: 2.3 KB per vehicle
(Python heap), so about 230 MB for 100K vehicles, spread across detector processes by
partition ([benchmark](../../evidence/benchmarks/m14-detector-memory.json)). An earlier
version kept each vehicle's whole last event (5.0 KB) and was OOM-killed at 100K vehicles
([load tests](../performance/load-tests.md#what-the-load-tests-found-and-fixed)).

---

## A8. Rule calibration: P(failure within 7 days | rule fired)

**Problem.** A rule firing is not a probability. The planner needs "how likely is this
vehicle to fail within the week, and by when".

**Method** (`evaluate.calibrate`, `scripts/build_calibration.py`). From the back-test
against ground truth, for each rule: of the alerts it opened, how many were followed by a
failure of the same mode on the same vehicle within 168 h (real time). Alerts whose 168 h
window runs past the end of the simulation are excluded (right-censoring), so "no failure
seen yet" is never counted as "no failure". Each rate has a 95% **Wilson** interval (valid
at 0 and 1, unlike the normal approximation). The deadline for planning is the rule's
**10th-percentile** time to failure (nearest rank), which is conservative. Rules with fewer
than 10 eligible alerts use the pooled rate instead of their own noisy estimate.

**Held-out check.** Fitted on seed 42 and scored on seed 7 with the **Brier score**
(mean squared error of the probability), against a no-skill forecast that always predicts
the held-out base rate.

**Measured** ([fit](../../evidence/benchmarks/m7-calibration-fit-seed42.json),
[held-out](../../evidence/benchmarks/m7-calibration-holdout-seed7.json),
[shipped file](../../apps/stream-processor/src/prognos_stream/calibration/rules-v1.json)):
- 315 held-out alerts. Base rate of failure after an alert: 98.1%.
- Brier score 0.0226, against 0.0187 for the no-skill forecast. **The per-rule
  probabilities do not beat the base rate.** In this simulated world almost every detected
  fault progresses to failure, so there is little for probabilities to separate.
- 23 of 24 fitted rule probabilities fall inside the held-out 95% interval.
- What the calibration does add is **deadlines**. For example, the p10 time to failure is
  2.4 h after COOLANT_OVERHEAT but 99.9 h after HV_CELL_IMBALANCE. The planner schedules on
  these deadlines.
- In real fleets, many warnings do not end in failure. The same code measures that as soon
  as real outcomes (work-order results) exist; the M8 model is scored the same way.

## A9. Capacity-aware maintenance planner (greedy by value)

**Problem.** Given at-risk vehicles and workshops with a daily capacity, decide who goes
where and when, before the predicted failure.

**Algorithm** (`planner.py`):
1. **One candidate per (vehicle, failure mode).** Risk is the **max** over its alerts,
   because correlated evidence must not be multiplied as if it were independent
   (noisy-OR would). The deadline is the tightest of: the alert's own estimate (for
   example, tyre hours to critical) or the rule's p10, minus the time since the alert.
2. **Value.** `p × (unplanned − planned + downtime/day × extra days)` when the tenant's
   costs are **sourced**. Costs tagged `PLACEHOLDER` are never turned into money: the value
   is `p × severity weight` and `expected_cost_avoided` stays NULL.
3. **Greedy assignment.** Sort by value, then earliest deadline, then vehicle id (O(n log n);
   the id makes the order deterministic). For each candidate, over the tenant's K = 3
   nearest workshops (haversine), take the earliest day with free capacity on or before
   the deadline, and the nearest workshop on that day. Otherwise take the earliest slot in
   the 7-day horizon and flag it **late**. If the horizon is full, the vehicle stays
   unscheduled and the gauge reports it.
   Total cost: O(n log n + n·K·D) for n candidates, K workshops and D days.
4. **Persistence.** One transaction under an advisory lock. A partial unique index allows
   one active work order per (vehicle, mode), and `ON CONFLICT DO NOTHING` makes re-runs
   and a second replica harmless. Every proposal writes an `audit_log` row: actor `system`,
   action `work_order.propose`, with the reasons.

**Why greedy.** Assigning vehicles to workshop-days is an assignment problem that an ILP or
min-cost flow would solve optimally. Greedy is predictable, explainable ("most valuable
first, nearest free slot"), and runs in milliseconds. It never gives a slot to a
lower-value vehicle while a higher-value vehicle that could use it is still waiting.
Optimality is not measured.

**Measured:** tested in unit tests (ordering, capacity, late, overflow, tenant isolation,
placeholder costs) and against real PostgreSQL (idempotent re-run, audit trail). On the
100K seed with 5,000 open alerts a cycle takes 0.90 s, and a re-run 28 ms with nothing new
proposed ([evidence](../../evidence/benchmarks/m7-planner-100k.json)).

## A10. Emerging-fault radar (cohort DTC rates, exact Poisson tail, Bonferroni)

**Problem.** A bad firmware release or parts batch shows up as the *same* DTC across many
vehicles of one cohort. No single-vehicle rule can see it.

**Algorithm** (`radar.py`). Tumbling 1-hour **event-time** windows, closed by a watermark
(the maximum event time minus 120 s of allowed lateness). Later events are counted and dropped.
In each window, for every DTC and cohort:
- `k` is the number of **distinct vehicles** in the cohort that reported the DTC, and `n`
  the cohort's registered vehicles. Counting distinct vehicles stops one chatty vehicle
  from faking a pattern.
- **Like-for-like baselines.** A firmware cohort is compared with the same model on other
  firmware; a model is compared with other models of the **same powertrain** (HV codes
  exist only on EVs).
- `p0 = (K + 0.5)/(N + 1)`, a smoothed baseline rate, so a zero baseline can still be tested.
- The p-value is the exact tail `P(X ≥ k)` for `X ~ Poisson(n·p0)`, computed in log space
  with a direct tail sum so it does not cancel to 0.
- **Bonferroni** correction multiplies by the number of tests in the window. A signal fires
  when k ≥ 5, the rate ratio is at least 3× and the adjusted p < 0.001.

**Data structure.** `dict[dtc][cohort] → set(vehicle_id)` for open windows only. A
**Count-Min Sketch was rejected**: there are only about 9 models × 2 firmwares × 20 DTCs
of keys, so exact sets take a few MB, and a sketch would add error (over-counting) with no
memory saved. Events without a DTC are skipped by a byte check, without JSON parsing.

**Measured** (simulated fleet; the simulator's `firmware_defect` scenario makes Lyra EV7V4
on firmware 2026.7 raise U0100 on 0.2% of its events):
- **Offline back-test** ([evidence](../../evidence/benchmarks/m7-radar-backtest.json)): 3,000
  vehicles and 3 simulated hours (3.2 M events), run twice with the same seed:
  - **Defect run.** A firmware-level signal for EV7V4 2026.7 / U0100 fired in all 4 windows,
    the first at 0.78 h. In the first window, 73 of 175 vehicles were affected against 6 of
    188 on the other firmware: 12× the baseline, adjusted p = 3e-50. A model-level signal
    for EV7V4 also fired. There were **0 other signals**.
  - **Control run** (no defect, same fleet and background noise): **0 signals**.
- **Hot path** ([evidence](../../evidence/benchmarks/m7-radar-hot-path.json)): 695 ns per
  canonical event (about 1.4 M events/s), counting the byte check and parsing only DTC events.
  Kafka consumption is not included. This is why a single radar consumer is enough for
  100K events/s.
- **Live, full stack** ([evidence](../../evidence/benchmarks/m7-radar-live.json)): 100K
  vehicles at 10K events/s through Kafka for 8 minutes, with 5-minute windows. The only
  complete window raised two signals and nothing else:
  - U0100 on EV7V4 2026.7: 384 of 6,082 vehicles, 12.5× the other firmware.
  - U0100 on EV7V4 (all firmware): 414 of 12,122 vehicles, 7.0× the other BEV models.

## Measured results (simulator)

All measured in a Linux container, 4 vCPU / 15 GB, x86_64 — **not** the target MacBook Air.

| Test | Result | Evidence |
|---|---|---|
| Generation only (null publisher), 100K vehicles, 1 worker | **100,140 ev/s** (1.0× real time), 330 MB RSS | `evidence/benchmarks/m3-simulator-benchmark.json` |
| … 2 workers | 192,924 ev/s | same |
| … 4 workers | **321,527 ev/s** (3.2× real time), 169 MB RSS/worker | same |
| Live into Kafka, 100K vehicles @ 100K ev/s, 3 workers, 60 s | 6,000,000 events at target rate, **lag 0 s**, 0 back-pressure waits, 0 delivery failures | `evidence/benchmarks/m3-simulator-kafka-live-100k.json` |
| Reconciliation of the live run | Kafka `telemetry.raw` holds **6,059,746 = 6,000,000 events + 59,746 injected duplicates**; `sim.truth` holds 1,065 = 1,065 onsets | same + offsets query in PR |
| 3× burst into Kafka (300K ev/s target), 4 workers, 30 s | **Not sustained on this box**: 173K ev/s average, fell up to 19.7 s behind; **no loss** (9,089,067 of 9,089,067 stored) | `evidence/benchmarks/m3-simulator-kafka-burst-300k.json` |
| Average raw message size | 535 bytes (JSON, before lz4) | all runs |

The burst shortfall is CPU contention (4 simulator workers and the Kafka broker on 4 vCPUs), not
Kafka back-pressure (0 waits). The 300K burst is re-measured in M14 on hardware where the
broker and the generator do not share cores.

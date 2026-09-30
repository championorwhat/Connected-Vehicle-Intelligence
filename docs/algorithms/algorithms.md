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

---

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

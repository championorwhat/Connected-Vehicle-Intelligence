"""Vectorised fleet state: driving physics, vehicle signals and failure degradation.

All per-vehicle state lives in numpy arrays, so one `step()` advances every
vehicle in a shard with ~40 array operations: O(n) per tick with a small
constant, no per-vehicle Python objects in the hot path.

Degradation model: a vehicle with an active fault has an onset time t0 and a
failure time tf. Progress p = clip((t - t0) / (tf - t0), 0, 1) drives
mode-specific signal shifts (mostly convex, p**2: slow at first, then fast).
At t >= tf the vehicle breaks down (ground truth FAILURE), stays immobile for a
downtime, is repaired, and the fault is cleared. Onset and failure times are
the ground truth used later to evaluate the predictive model honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray

from prognos_common.catalog import FAILURE_MODES, MODEL_BY_CODE, OEMS, Powertrain
from prognos_common.roster import Vehicle

F64 = NDArray[np.float64]
BOOL = NDArray[np.bool_]
I64 = NDArray[np.int64]

OEM_INDEX = {o.code: i for i, o in enumerate(OEMS)}
MODE_INDEX = {fm.code: i for i, fm in enumerate(FAILURE_MODES)}
NO_FAULT = -1
COOLING, MISFIRE, LV_BATTERY, TYRE_LEAK, HV_THERMAL = (
    MODE_INDEX[c]
    for c in (
        "COOLING_FAILURE",
        "IGNITION_MISFIRE",
        "LV_BATTERY_FAILURE",
        "TYRE_SLOW_LEAK",
        "HV_BATTERY_THERMAL",
    )
)

# Driving patterns: 0 urban delivery, 1 highway / long-haul, 2 mixed.
URBAN, HIGHWAY, MIXED = 0, 1, 2
TARGET_LO = np.array([10.0, 55.0, 20.0])
TARGET_HI = np.array([50.0, 95.0, 80.0])
TRIP_MEAN_S = np.array([1200.0, 5400.0, 2700.0])
PARK_MEAN_S = np.array([900.0, 2700.0, 1800.0])
RADIUS_KM = np.array([15.0, 120.0, 40.0])
NOMINAL_TYRE_KPA = {"car": 240.0, "van": 350.0, "light_truck": 380.0, "truck": 620.0}

# Degradation duration ranges in days (sim time, divided by fault_time_scale).
FAULT_DAYS = np.array([[2.0, 7.0], [3.0, 10.0], [5.0, 15.0], [0.5, 3.0], [7.0, 21.0]])
DOWNTIME_HOURS = (8.0, 48.0)

# Event types (index into EVENT_TYPES).
EVENT_TYPES = ("PERIODIC", "IGNITION_ON", "IGNITION_OFF", "HARSH_BRAKE", "BREAKDOWN")
EVT_PERIODIC, EVT_IGN_ON, EVT_IGN_OFF, EVT_HARSH, EVT_BREAKDOWN = range(5)

KMH_PER_S_ACCEL = 9.0  # ~2.5 m/s^2
KMH_PER_S_BRAKE = 11.0  # ~3 m/s^2
KMH_PER_S_HARSH = 20.0  # ~5.5 m/s^2
DAY_S = 86_400.0


@dataclass(slots=True)
class GroundTruth:
    kind: str  # FAULT_ONSET | FAILURE
    vehicle_index: int
    failure_mode: int
    onset_ts: float
    failure_ts: float
    scenario: bool = False


@dataclass
class Fleet:
    """State for one shard of vehicles. Construct with `Fleet.create`.

    Per-vehicle arrays (lat, lon, speed, fault_mode, ...) are plain instance
    attributes created in `_init_arrays`; `__getattr__` only exists to type them.
    """

    vehicles: tuple[Vehicle, ...]
    rng: np.random.Generator
    fault_rate: float
    fault_time_scale: float
    n: int = 0
    ground_truth: list[GroundTruth] = field(default_factory=list)

    # ---------------------------------------------------------------- setup
    @classmethod
    def create(
        cls,
        vehicles: tuple[Vehicle, ...],
        *,
        seed: int,
        start_ts: float,
        fault_rate: float,
        fault_time_scale: float = 1.0,
    ) -> Fleet:
        fleet = cls(vehicles, np.random.default_rng(seed), fault_rate, fault_time_scale)
        fleet._init_arrays(start_ts)
        fleet._seed_initial_faults(start_ts)
        return fleet

    if TYPE_CHECKING:

        def __getattr__(self, name: str) -> Any: ...
        def __setattr__(self, name: str, value: Any) -> None: ...

    def _init_arrays(self, start_ts: float) -> None:
        v = self.vehicles
        n = self.n = len(v)
        rng = self.rng
        models = [MODEL_BY_CODE[x.model_code] for x in v]
        powertrain = np.array([m.powertrain.value for m in models])
        classes = np.array([m.vehicle_class for m in models])

        pattern = np.full(n, MIXED)
        pattern[classes == "van"] = np.where(
            rng.random((classes == "van").sum()) < 0.7, URBAN, MIXED
        )
        pattern[classes == "truck"] = np.where(
            rng.random((classes == "truck").sum()) < 0.6, HIGHWAY, MIXED
        )
        home_lat = np.array([x.home_latitude for x in v])
        home_lon = np.array([x.home_longitude for x in v])
        driving = rng.random(n) < 0.45
        a: dict[str, np.ndarray] = {
            "oem": np.array([OEM_INDEX[x.oem_code] for x in v], dtype=np.int8),
            "has_engine": np.isin(powertrain, [Powertrain.ICE.value, Powertrain.HEV.value]),
            "has_hv": np.isin(powertrain, [Powertrain.HEV.value, Powertrain.BEV.value]),
            "is_bev": powertrain == Powertrain.BEV.value,
            "pattern": pattern,
            "aggressiveness": rng.beta(2.0, 5.0, n),
            "home_lat": home_lat,
            "home_lon": home_lon,
            "lat": home_lat + rng.normal(0, 0.02, n),
            "lon": home_lon + rng.normal(0, 0.02, n),
            "heading": rng.uniform(0, 360, n),
            "speed": np.where(driving, rng.uniform(10, 60, n), 0.0),
            "target": np.zeros(n),
            "odometer": rng.uniform(5_000, 180_000, n),
            "driving": driving,
            "mode_remaining": rng.exponential(PARK_MEAN_S[pattern]),
            "pending_event": np.zeros(n, dtype=np.int8),
            "seq": rng.integers(1, 1_000_000, n, dtype=np.int64),
            "coolant_base": np.where(driving, 88.0, 32.0),
            "fuel": rng.uniform(20, 95, n),
            "soc": rng.uniform(30, 95, n),
            "soh": rng.uniform(88, 99.5, n),
            "tyre_nominal": np.array([NOMINAL_TYRE_KPA[c] for c in classes]),
            "tyre_offset": rng.normal(0, 4, (n, 4)),
            "fault_mode": np.full(n, NO_FAULT, dtype=np.int8),
            "fault_onset": np.zeros(n),
            "fault_fail": np.zeros(n),
            "fault_aux": rng.integers(0, 4, n),  # which tyre / cylinder is affected
            "broken": np.zeros(n, dtype=bool),
            "broken_until": np.zeros(n),
            "eligible": np.array(
                [[Powertrain(p) in fm.powertrains for fm in FAILURE_MODES] for p in powertrain]
            ),
        }
        a["target"] = np.where(driving, a["speed"], 0.0)
        for name, array in a.items():
            setattr(self, name, array)

    def _fault_duration_s(self, modes: NDArray[np.int64]) -> F64:
        lo, hi = FAULT_DAYS[modes, 0], FAULT_DAYS[modes, 1]
        return cast(F64, self.rng.uniform(lo, hi) * DAY_S / self.fault_time_scale)

    def _pick_modes(self, idx: I64) -> I64:
        """Uniformly choose an eligible failure mode for each vehicle index."""
        weights = self.eligible[idx].astype(float)
        weights /= weights.sum(axis=1, keepdims=True)
        cumulative = weights.cumsum(axis=1)
        draws = self.rng.random((len(idx), 1))
        return cast(I64, (draws > cumulative).sum(axis=1).astype(np.int64))

    def _start_faults(self, idx: I64, t: float, progress: F64, *, scenario: bool = False) -> None:
        modes = self._pick_modes(idx)
        duration = self._fault_duration_s(modes)
        onset = t - progress * duration
        self.fault_mode[idx] = modes
        self.fault_onset[idx] = onset
        self.fault_fail[idx] = onset + duration
        for i, m, o, f in zip(idx, modes, onset, onset + duration, strict=True):
            self.ground_truth.append(
                GroundTruth("FAULT_ONSET", int(i), int(m), float(o), float(f), scenario)
            )

    def _seed_initial_faults(self, t: float) -> None:
        chosen = np.flatnonzero(self.rng.random(self.n) < self.fault_rate)
        if len(chosen):
            self._start_faults(chosen, t, self.rng.uniform(0.0, 0.9, len(chosen)))

    def force_fault(self, index: int, mode: int, t: float, seconds_to_failure: float) -> None:
        """Deterministic fault for demo scenarios (ignores fault_time_scale)."""
        if not self.eligible[index, mode]:
            raise ValueError(f"vehicle {index} cannot have failure mode {mode}")
        self.fault_mode[index] = mode
        self.fault_onset[index] = t
        self.fault_fail[index] = t + seconds_to_failure
        self.ground_truth.append(
            GroundTruth("FAULT_ONSET", index, mode, t, t + seconds_to_failure, scenario=True)
        )

    # ---------------------------------------------------------------- dynamics
    def progress(self, t: float) -> F64:
        active = self.fault_mode != NO_FAULT
        span = np.maximum(self.fault_fail - self.fault_onset, 1e-9)
        return np.where(active, np.clip((t - self.fault_onset) / span, 0.0, 1.0), 0.0)

    def step(self, t: float, dt: float) -> None:
        rng, n = self.rng, self.n
        self._handle_breakdowns_and_repairs(t)

        # New fault onsets (steady-state fraction of degrading vehicles ~= fault_rate).
        healthy = (self.fault_mode == NO_FAULT) & ~self.broken
        mean_duration = FAULT_DAYS.mean() * DAY_S / self.fault_time_scale
        hazard = self.fault_rate / max(1.0 - self.fault_rate, 1e-9) / mean_duration
        onset = np.flatnonzero(healthy & (rng.random(n) < hazard * dt))
        if len(onset):
            self._start_faults(onset, t, np.zeros(len(onset)))

        # Trip / park state machine.
        self.mode_remaining -= dt
        switch = np.flatnonzero((self.mode_remaining <= 0) & ~self.broken)
        if len(switch):
            now_driving = ~self.driving[switch]
            self.driving[switch] = now_driving
            pat = self.pattern[switch]
            mean = np.where(now_driving, TRIP_MEAN_S[pat], PARK_MEAN_S[pat])
            self.mode_remaining[switch] = rng.exponential(mean) + 60.0
            self.pending_event[switch] = np.where(now_driving, EVT_IGN_ON, EVT_IGN_OFF)

        drv = self.driving & ~self.broken
        pat = self.pattern

        # Target speed: occasional re-targeting, urban stops, zero when parked.
        retarget = drv & (rng.random(n) < dt / 30.0)
        self.target[retarget] = rng.uniform(TARGET_LO[pat[retarget]], TARGET_HI[pat[retarget]])
        stop = drv & (pat != HIGHWAY) & (rng.random(n) < dt / 90.0)
        self.target[stop] = 0.0
        self.target[~drv] = 0.0
        restart = drv & (self.target == 0) & (rng.random(n) < dt / 20.0)
        self.target[restart] = rng.uniform(TARGET_LO[pat[restart]], TARGET_HI[pat[restart]])

        # Speed follows target within acceleration limits; aggressive drivers brake hard.
        diff = self.target - self.speed
        speed = self.speed + np.clip(diff, -KMH_PER_S_BRAKE * dt, KMH_PER_S_ACCEL * dt)
        harsh = drv & (self.speed > 30) & (rng.random(n) < self.aggressiveness * dt / 600.0)
        speed[harsh] = self.speed[harsh] - KMH_PER_S_HARSH * dt
        self.target[harsh] = speed[harsh]
        self.pending_event[harsh & (self.pending_event == EVT_PERIODIC)] = EVT_HARSH
        speed = np.where(drv, np.clip(speed + rng.normal(0, 0.4, n), 0, 130), 0.0)
        self.speed[:] = speed

        # Heading random walk; steer home when outside the pattern's operating radius.
        self.heading[drv] += rng.normal(0, 6.0 * np.sqrt(dt), drv.sum())
        lat_rad = np.radians(self.lat)
        dy_km = (self.home_lat - self.lat) * 111.32
        dx_km = (self.home_lon - self.lon) * 111.32 * np.cos(lat_rad)
        away = drv & (np.hypot(dx_km, dy_km) > RADIUS_KM[pat])
        self.heading[away] = np.degrees(np.arctan2(dx_km[away], dy_km[away]))
        self.heading %= 360.0

        dist_km = speed * dt / 3600.0
        hdg = np.radians(self.heading)
        self.lat += dist_km * np.cos(hdg) / 111.32
        self.lon += dist_km * np.sin(hdg) / (111.32 * np.cos(lat_rad))
        self.odometer += dist_km

        # Energy and thermal state.
        running = drv
        target_c = np.where(running & self.has_engine, 88.0 + speed * 0.03, 32.0)
        self.coolant_base += (target_c - self.coolant_base) * min(1.0, dt / 240.0)
        ice = self.has_engine & ~self.has_hv
        self.fuel[ice] -= dist_km[ice] * 0.12 / 60.0 * 100.0
        refuel = ice & ~running & (self.fuel < 12)
        self.fuel[refuel] = 95.0
        bev = self.is_bev
        self.soc[bev] -= dist_km[bev] * 0.2 / 60.0 * 100.0
        charging = bev & ~running & (self.soc < 85)
        self.soc[charging] += 0.018 * dt
        hev = self.has_hv & self.has_engine
        self.soc[hev] = np.clip(self.soc[hev] + rng.normal(0, 0.05, hev.sum()), 40, 75)
        np.clip(self.soc, 0, 100, out=self.soc)

    def _handle_breakdowns_and_repairs(self, t: float) -> None:
        repaired = np.flatnonzero(self.broken & (t >= self.broken_until))
        if len(repaired):
            self.broken[repaired] = False
            self.fault_mode[repaired] = NO_FAULT
            self.tyre_offset[repaired] = self.rng.normal(0, 4, (len(repaired), 4))
            self.mode_remaining[repaired] = 60.0
        failing = np.flatnonzero(
            (self.fault_mode != NO_FAULT) & ~self.broken & (t >= self.fault_fail)
        )
        if len(failing):
            self.broken[failing] = True
            self.driving[failing] = False
            self.speed[failing] = 0.0
            hours = self.rng.uniform(*DOWNTIME_HOURS, len(failing))
            self.broken_until[failing] = t + hours * 3600.0 / self.fault_time_scale
            self.pending_event[failing] = EVT_BREAKDOWN
            for i in failing:
                self.ground_truth.append(
                    GroundTruth(
                        "FAILURE",
                        int(i),
                        int(self.fault_mode[i]),
                        float(self.fault_onset[i]),
                        float(self.fault_fail[i]),
                    )
                )

    # ---------------------------------------------------------------- signals
    def signals(self, idx: I64, t: float) -> dict[str, np.ndarray]:
        """Observable signals for the given vehicles at time t (base physics + fault effects)."""
        rng = self.rng
        k = len(idx)
        mode = self.fault_mode[idx]
        p = self.progress(t)[idx]
        running = self.driving[idx] & ~self.broken[idx]
        speed = self.speed[idx]
        has_engine = self.has_engine[idx]

        misfire_std = np.where(mode == MISFIRE, 180.0 * p**2, 0.0)
        rpm = np.where(
            running & has_engine,
            750.0 + speed * 28.0 + rng.normal(0, 1, k) * (40.0 + misfire_std),
            0.0,
        )
        coolant = (
            self.coolant_base[idx]
            + np.where(mode == COOLING, 32.0 * p**2, 0.0)
            + rng.normal(0, 0.6, k)
        )
        lv = np.where(running, 14.1, 12.6) + rng.normal(0, 0.05, k)
        lv += np.where(mode == LV_BATTERY, np.where(running, -0.4, -1.4) * p**1.5, 0.0)

        tyres = (
            self.tyre_nominal[idx, None]
            + self.tyre_offset[idx]
            + np.where(running, 8.0, 0.0)[:, None]
        )
        leak = np.where(mode == TYRE_LEAK, -0.38 * self.tyre_nominal[idx] * p, 0.0)
        tyres[np.arange(k), self.fault_aux[idx]] += leak
        tyres += rng.normal(0, 0.8, (k, 4))

        hv_fault = mode == HV_THERMAL
        charging = self.is_bev[idx] & ~running & (self.soc[idx] < 85)
        pack_temp = 27.0 + speed * 0.05 + charging * 6.0 + np.where(hv_fault, 18.0 * p**2, 0.0)
        pack_temp += rng.normal(0, 0.4, k)
        cell_delta = 8.0 + np.where(hv_fault, 140.0 * p**2, 0.0) + rng.gamma(2.0, 1.5, k)
        soh = self.soh[idx] - np.where(hv_fault, 6.0 * p, 0.0)

        return {
            "lat": self.lat[idx],
            "lon": self.lon[idx],
            "speed": speed,
            "heading": self.heading[idx],
            "odometer": self.odometer[idx],
            "ignition": running,
            "rpm": rpm,
            "coolant": coolant,
            "fuel": self.fuel[idx],
            "lv": lv,
            "tyres": tyres,
            "soc": self.soc[idx],
            "soh": soh,
            "pack_temp": pack_temp,
            "cell_delta": cell_delta,
            "mode": mode,
            "progress": p,
        }

    def dtcs(self, idx: I64, sig: dict[str, np.ndarray]) -> list[list[str]]:
        """Diagnostic trouble codes per event, from fault progress and signal thresholds."""
        rng = self.rng
        k = len(idx)
        codes: list[list[str]] = [[] for _ in range(k)]
        mode, p = sig["mode"], sig["progress"]
        r = rng.random(k)
        has_engine = self.has_engine[idx]
        tyre_min_ratio = sig["tyres"].min(axis=1) / self.tyre_nominal[idx]

        rules: list[tuple[BOOL, str | None]] = [
            ((mode == COOLING) & (p > 0.6) & (r < 0.1), "P0118"),
            (has_engine & (sig["coolant"] > 112.0), "P0217"),
            ((mode == MISFIRE) & (r < 0.5 * p**3), None),  # cylinder-specific, below
            ((mode == MISFIRE) & (p > 0.85) & (r < 0.3), "P0300"),
            (sig["lv"] < 11.9, "P0562"),
            (tyre_min_ratio < 0.8, None),  # tyre-specific, below
            ((mode == HV_THERMAL) & (p > 0.6) & (r < 0.2), "P0A7F"),
            ((mode == HV_THERMAL) & (p > 0.9), "P0A80"),
            (self.has_hv[idx] & (sig["cell_delta"] > 100.0) & (r < 0.1), "P0AFA"),
        ]
        for mask, code in rules:
            for j in np.flatnonzero(mask):
                if code is not None:
                    codes[j].append(code)
                elif mode[j] == MISFIRE:
                    codes[j].append(f"P030{self.fault_aux[idx[j]] + 1}")
                else:
                    codes[j].append(f"C1A1{int(sig['tyres'][j].argmin())}")
        # Background noise: benign real-world codes on ~0.05% of events.
        for j in np.flatnonzero(rng.random(k) < 0.0005):
            codes[j].append(str(rng.choice(["P0420", "P0455", "U0100"])))
        return codes

    def take_events(self, idx: I64) -> NDArray[np.int8]:
        """Return and clear pending event types; advance sequence numbers."""
        events = self.pending_event[idx].copy()
        self.pending_event[idx] = EVT_PERIODIC
        self.seq[idx] += 1
        return cast(NDArray[np.int8], events)

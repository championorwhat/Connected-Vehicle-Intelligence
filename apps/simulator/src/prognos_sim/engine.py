"""One simulator shard: advance the fleet, emit events, inject delivery anomalies.

Per tick (default 10 Hz):
  1. Fleet.step(t, dt): physics + degradation for every vehicle in the shard.
  2. Pick the next k vehicles round-robin, where k = rate * burst_multiplier * dt
     (fractional remainder carried to the next tick), so every vehicle reports
     at the same cadence.
  3. Encode each event in its OEM's format, then inject anomalies:
       network latency   event_ts = t - Exp(mean)            (all events)
       missing field     one field removed                     MISSING_FIELD_RATE
       malformed         one of 7 corruption kinds             MALFORMED_RATE
       out-of-order      held back 1..MAX_DELAY_SECONDS        OUT_OF_ORDER_RATE
       duplicate         re-sent now or 1..10 s later          DUPLICATE_RATE
  4. Release held-back events whose time has come (min-heap: O(log h) each).

Every injected anomaly is counted, so a downstream reconciliation job can
prove no data was lost: unique events = generated; duplicates, malformed and
delayed counts are known exactly.
"""

from __future__ import annotations

import heapq
import itertools
import logging
from collections import Counter
from typing import Any

import numpy as np
import orjson

from prognos_common.catalog import FAILURE_MODES
from prognos_common.roster import Vehicle
from prognos_sim.config import SimConfig
from prognos_sim.fleet import EVENT_TYPES, LV_BATTERY, Fleet, GroundTruth
from prognos_sim.formats import (
    ENCODERS,
    MALFORMED_KINDS,
    SCHEMAS,
    Columns,
    corrupt,
    drop_field,
    iso_utc,
)
from prognos_sim.publishers import Headers, Publisher

log = logging.getLogger(__name__)

OEM_CODES = ("ORION", "VEGA", "LYRA")
HEADERS: dict[str, Headers] = {
    code: [
        ("oem", code.encode()),
        ("schema", schema.encode()),
        ("content-type", b"application/json"),
    ]
    for code, schema in zip(OEM_CODES, SCHEMAS, strict=True)
}
HEADERS["ZETA"] = [("oem", b"ZETA"), ("content-type", b"application/json")]
DEMO_SECONDS_TO_FAILURE = 600.0


class ShardSimulator:
    def __init__(
        self,
        cfg: SimConfig,
        worker_id: int,
        vehicles: tuple[Vehicle, ...],
        publisher: Publisher,
        start_ts: float,
    ) -> None:
        self.cfg = cfg
        self.worker_id = worker_id
        self.vehicles = vehicles
        self.publisher = publisher
        seed = cfg.seed * 1_000 + worker_id
        self.fleet = Fleet.create(
            vehicles,
            seed=seed,
            start_ts=start_ts,
            fault_rate=cfg.fault_rate,
            fault_time_scale=cfg.fault_time_scale,
        )
        self.rng = np.random.default_rng(seed + 500_000)
        self.vins = np.array([v.vin for v in vehicles], dtype=object)
        self.vin_bytes = [v.vin.encode() for v in vehicles]
        self.rate = cfg.events_per_second / cfg.sim_workers
        self.cursor = 0
        self.carry = 0.0
        self.delayed: list[tuple[float, int, bytes, bytes, Headers]] = []
        self._tiebreak = itertools.count()
        self.counts: Counter[str] = Counter()
        self.demo_vins: list[str] = []
        if cfg.scenario == "demo" and worker_id == 0:
            self._start_demo(start_ts)
        self._publish_ground_truth()

    # ------------------------------------------------------------------ public
    def tick(self, t: float, dt: float, multiplier: float = 1.0) -> int:
        self.fleet.step(t, dt)
        self._publish_ground_truth()
        wanted = self.rate * multiplier * dt + self.carry
        k = int(wanted)
        self.carry = wanted - k
        if k:
            n = self.fleet.n
            idx = (self.cursor + np.arange(k)) % n
            self.cursor = (self.cursor + k) % n
            self._emit(idx, t)
        self._release_delayed(t)
        self.publisher.poll()
        return k

    def finish(self) -> None:
        """Release everything still held back, then flush the publisher."""
        self._release_delayed(float("inf"))
        remaining = self.publisher.flush()
        if remaining:
            log.error("worker %d: %d messages not delivered at shutdown", self.worker_id, remaining)
            self.counts["undelivered_at_shutdown"] += remaining

    def snapshot(self) -> dict[str, int]:
        return {**self.counts, **self.publisher.stats(), "held_back": len(self.delayed)}

    # ------------------------------------------------------------------ internals
    def _start_demo(self, t: float) -> None:
        """One vehicle per failure mode fails ~10-18 minutes after start."""
        chosen: set[int] = set()
        for mode in range(len(FAILURE_MODES)):
            # A distinct vehicle per mode (forcing a second fault on one vehicle would
            # overwrite the first). Engine faults stay on a long trip so misfire and
            # coolant signals are observable; the 12 V battery vehicle stays parked,
            # because a weak battery only shows at rest (the alternator masks it).
            candidates = [
                int(i) for i in np.flatnonzero(self.fleet.eligible[:, mode]) if int(i) not in chosen
            ]
            if not candidates:
                continue
            index = candidates[0]
            chosen.add(index)
            self.fleet.force_fault(index, mode, t, DEMO_SECONDS_TO_FAILURE + 120.0 * mode)
            self.fleet.driving[index] = mode != LV_BATTERY
            self.fleet.mode_remaining[index] = 3 * 3600.0
            self.demo_vins.append(self.vehicles[index].vin)
            log.info(
                "demo: %s will fail with %s", self.vehicles[index].vin, FAILURE_MODES[mode].code
            )

    def _publish_ground_truth(self) -> None:
        records: list[GroundTruth] = self.fleet.ground_truth
        for gt in records:
            v = self.vehicles[gt.vehicle_index]
            body = {
                "type": gt.kind,
                "vehicle_id": str(v.vehicle_id),
                "tenant_id": str(v.tenant_id),
                "vin": v.vin,
                "failure_mode": FAILURE_MODES[gt.failure_mode].code,
                "onset_ts": iso_utc(gt.onset_ts),
                "failure_ts": iso_utc(gt.failure_ts),
                "scenario": gt.scenario,
            }
            self.publisher.publish(
                self.cfg.ground_truth_topic,
                self.vin_bytes[gt.vehicle_index],
                orjson.dumps(body),
                [],
            )
            self.counts[f"ground_truth_{gt.kind.lower()}"] += 1
        records.clear()

    def _columns(self, idx: np.ndarray, t: float) -> Columns:
        f = self.fleet
        events = f.take_events(idx)
        sig = f.signals(idx, t)
        latency = self.rng.exponential(self.cfg.network_delay_ms_mean / 1000.0, len(idx))
        return Columns(
            vin=self.vins[idx].tolist(),
            seq=f.seq[idx].tolist(),
            ts=(t - latency).tolist(),
            evt=[EVENT_TYPES[e] for e in events],
            lat=np.round(sig["lat"], 6).tolist(),
            lon=np.round(sig["lon"], 6).tolist(),
            speed=np.round(sig["speed"], 1).tolist(),
            heading=sig["heading"].astype(np.int64).tolist(),
            odometer=np.round(sig["odometer"], 2).tolist(),
            ignition=sig["ignition"].tolist(),
            rpm=sig["rpm"].astype(np.int64).tolist(),
            coolant=np.round(sig["coolant"], 1).tolist(),
            fuel=np.round(sig["fuel"], 1).tolist(),
            lv=np.round(sig["lv"], 2).tolist(),
            tyres=np.round(sig["tyres"], 1).tolist(),
            soc=np.round(sig["soc"], 1).tolist(),
            soh=np.round(sig["soh"], 1).tolist(),
            pack_temp=np.round(sig["pack_temp"], 1).tolist(),
            cell_delta=sig["cell_delta"].astype(np.int64).tolist(),
            dtcs=f.dtcs(idx, sig),
            has_engine=f.has_engine[idx].tolist(),
            has_hv=f.has_hv[idx].tolist(),
        )

    def _emit(self, idx: np.ndarray, t: float) -> None:
        cfg, rng = self.cfg, self.rng
        k = len(idx)
        cols = self._columns(idx, t)
        oems = self.fleet.oem[idx].tolist()
        malformed = (rng.random(k) < cfg.malformed_rate).tolist()
        missing = (rng.random(k) < cfg.missing_field_rate).tolist()
        duplicate = (rng.random(k) < cfg.duplicate_rate).tolist()
        delayed = (rng.random(k) < cfg.out_of_order_rate).tolist()
        kinds = rng.integers(len(MALFORMED_KINDS), size=k).tolist()
        publish = self.publisher.publish
        topic = cfg.raw_topic
        counts = self.counts
        idx_list = idx.tolist()

        for j in range(k):
            oem = oems[j]
            payload: dict[str, Any] = ENCODERS[oem](cols, j)
            oem_header = OEM_CODES[oem]
            if malformed[j]:
                kind = MALFORMED_KINDS[kinds[j]]
                value, oem_header = corrupt(oem, payload, kind, rng)
                counts["malformed"] += 1
                counts[f"malformed_{kind}"] += 1
            else:
                if missing[j]:
                    drop_field(oem, payload, rng)
                    counts["missing_field"] += 1
                value = orjson.dumps(payload)
            key = self.vin_bytes[idx_list[j]]
            headers = HEADERS[oem_header]
            if delayed[j]:
                release = t + rng.uniform(1.0, cfg.max_delay_seconds)
                heapq.heappush(self.delayed, (release, next(self._tiebreak), key, value, headers))
                counts["out_of_order"] += 1
            else:
                publish(topic, key, value, headers)
            if duplicate[j]:
                counts["duplicates"] += 1
                if rng.random() < 0.5:
                    publish(topic, key, value, headers)
                else:
                    release = t + rng.uniform(1.0, 10.0)
                    heapq.heappush(
                        self.delayed, (release, next(self._tiebreak), key, value, headers)
                    )
        counts["events_generated"] += k

    def _release_delayed(self, t: float) -> None:
        heap, publish, topic = self.delayed, self.publisher.publish, self.cfg.raw_topic
        while heap and heap[0][0] <= t:
            _, _, key, value, headers = heapq.heappop(heap)
            publish(topic, key, value, headers)

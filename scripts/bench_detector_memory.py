"""Detector state memory per vehicle (M14): uv run python scripts/bench_detector_memory.py [N]

Feeds the real Detector three realistic canonical events per vehicle (2 % carry a DTC)
and reports the Python heap growth traced by tracemalloc. The interpreter baseline and
the Kafka client's buffers are not included, so a container's RSS is higher.
"""

from __future__ import annotations

import gc
import sys
import time
import tracemalloc
import uuid
from typing import Any

import orjson

from prognos_stream.detector import Detector


def event(i: int, seq: int, ts: float) -> dict[str, Any]:
    """A canonical event as the detector receives it (decoded from Kafka JSON)."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))
    body = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": f"0f8f96fc-827c-4360-b22c-e9923da543{i % 20:02d}",
        "vehicle_id": str(uuid.UUID(int=i)), "vin": f"PG1CT1A59RC{i:06d}", "oem": "oem_a",
        "schema_version": 2, "seq": seq, "event_ts": f"{stamp}.000Z",
        "ingest_ts": f"{stamp}.300Z", "event_type": "PERIODIC",
        "latitude": 12.97 + i / 1e6, "longitude": 77.59, "speed_kmh": 50.0, "heading_deg": 90,
        "odometer_km": 12345.6, "ignition_on": True, "engine_rpm": 2150,
        "coolant_temp_c": 89.5, "fuel_level_pct": 60.0, "lv_battery_v": 14.1,
        "hv_soc_pct": None, "hv_soh_pct": None, "hv_pack_temp_c": None,
        "hv_cell_delta_mv": None, "tyre_fl_kpa": 240.0, "tyre_fr_kpa": 241.0,
        "tyre_rl_kpa": 239.0, "tyre_rr_kpa": 240.5,
        "dtc_codes": ["P0300"] if i % 50 == 0 else [],
    }  # fmt: skip
    decoded: dict[str, Any] = orjson.loads(orjson.dumps(body))
    return decoded


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    t0 = time.time() - 3600
    tracemalloc.start()
    detector = Detector()
    base = tracemalloc.get_traced_memory()[0]
    for i in range(n):
        for k in range(3):
            detector.process(event(i, k, t0 + 10 * k), i % 6, t0 + 10 * k + 0.5)
    gc.collect()
    used = tracemalloc.get_traced_memory()[0] - base
    per_vehicle = used / n
    fleet_mb = per_vehicle * 1e5 / 1e6
    print(f"{n} vehicles (2% with a DTC): {used / 1e6:.1f} MB traced, "
          f"{per_vehicle:.0f} B/vehicle, 100K vehicles ~ {fleet_mb:.0f} MB")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

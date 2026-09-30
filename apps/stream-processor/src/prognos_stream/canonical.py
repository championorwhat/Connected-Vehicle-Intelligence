"""Canonical telemetry event, schema version 1.

Every OEM payload is mapped to this flat, metric, UTC shape. It is emitted as
JSON on `telemetry.canonical`, keyed by vehicle_id, and is directly readable by
ClickHouse (JSONEachRow) — see docs/api/asyncapi.yaml and
packages/schemas/canonical-telemetry-v1.schema.json.

Evolution rules: new fields are optional and additive (minor change); renaming,
removing or changing the meaning/unit of a field requires schema_version 2 and a
dual-publish period.
"""

from __future__ import annotations

from hashlib import blake2b

SCHEMA_VERSION = 1

REQUIRED = ("vin", "event_ts", "seq", "latitude", "longitude", "speed_kmh")

# field -> (min, max) for numeric range validation (inclusive).
RANGES: dict[str, tuple[float, float]] = {
    "latitude": (-90.0, 90.0),
    "longitude": (-180.0, 180.0),
    "speed_kmh": (0.0, 250.0),
    "heading_deg": (0.0, 360.0),
    "odometer_km": (0.0, 3_000_000.0),
    "engine_rpm": (0.0, 10_000.0),
    "coolant_temp_c": (-40.0, 150.0),
    "fuel_level_pct": (0.0, 100.0),
    "lv_battery_v": (0.0, 20.0),
    "hv_soc_pct": (0.0, 100.0),
    "hv_soh_pct": (0.0, 100.0),
    "hv_pack_temp_c": (-40.0, 100.0),
    "hv_cell_delta_mv": (0.0, 2_000.0),
    "tyre_fl_kpa": (0.0, 1_500.0),
    "tyre_fr_kpa": (0.0, 1_500.0),
    "tyre_rl_kpa": (0.0, 1_500.0),
    "tyre_rr_kpa": (0.0, 1_500.0),
}

EVENT_TYPES = frozenset({"PERIODIC", "IGNITION_ON", "IGNITION_OFF", "HARSH_BRAKE", "BREAKDOWN"})


def event_id(vehicle_id: str, seq: int) -> str:
    """Deterministic 128-bit id of (vehicle_id, seq), formatted as a UUID string.

    A redelivered or duplicated event gets the same id, so every downstream sink can
    be idempotent on event_id. BLAKE2b-128 instead of uuid5 (SHA-1 + UUID object):
    same determinism, ~5x cheaper on the hot path (profiled: uuid5 was 7 us/event).
    """
    h = blake2b(f"{vehicle_id}:{seq}".encode(), digest_size=16).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

"""OEM payload encoders, corruption and field-dropping.

Three deliberately different wire formats, as real OEM clouds deliver them:

ORION  flat_json_metric      flat keys, SI units, ISO-8601 UTC ("Z")
VEGA   nested_json_imperial  nested sections, mph / miles / psi / °F, IST offset
                             timestamps, DTCs packed in one comma-separated string
LYRA   signal_list_json      [{"name","value","unit"}] list, bar / volts, epoch ms

The normaliser (M4/M5) must map all three to the canonical schema; the
differences here are what make that job non-trivial.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import orjson

MALFORMED_KINDS = (
    "truncated",
    "not_json",
    "bad_vin",
    "out_of_range",
    "wrong_type",
    "bad_timestamp",
    "unknown_oem",
)

SCHEMAS = ("orion.v1", "vega.2.3", "lyra.v1")
_IST_OFFSET_S = 19_800

_utc_prefix: dict[int, str] = {}
_ist_prefix: dict[int, str] = {}


def iso_utc(ts: float) -> str:
    """ISO-8601 UTC with millisecond precision; per-second prefix is cached."""
    sec = int(ts)
    prefix = _utc_prefix.get(sec)
    if prefix is None:
        if len(_utc_prefix) > 8192:
            _utc_prefix.clear()
        prefix = _utc_prefix[sec] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec))
    return f"{prefix}.{int((ts - sec) * 1000):03d}Z"


def iso_ist(ts: float) -> str:
    """ISO-8601 in India Standard Time (+05:30), as VEGA's cloud reports it."""
    sec = int(ts)
    prefix = _ist_prefix.get(sec)
    if prefix is None:
        if len(_ist_prefix) > 8192:
            _ist_prefix.clear()
        prefix = _ist_prefix[sec] = time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.gmtime(sec + _IST_OFFSET_S)
        )
    return f"{prefix}.{int((ts - sec) * 1000):03d}+05:30"


@dataclass(slots=True)
class Columns:
    """Per-event values for one batch, already rounded and converted to Python lists."""

    vin: list[str]
    seq: list[int]
    ts: list[float]
    evt: list[str]
    lat: list[float]
    lon: list[float]
    speed: list[float]
    heading: list[int]
    odometer: list[float]
    ignition: list[bool]
    rpm: list[int]
    coolant: list[float]
    fuel: list[float]
    lv: list[float]
    tyres: list[list[float]]
    soc: list[float]
    soh: list[float]
    pack_temp: list[float]
    cell_delta: list[int]
    dtcs: list[list[str]]
    has_engine: list[bool]
    has_hv: list[bool]


def encode_orion(c: Columns, j: int) -> dict[str, Any]:
    d: dict[str, Any] = {
        "vin": c.vin[j],
        "ts": iso_utc(c.ts[j]),
        "seq": c.seq[j],
        "evt": c.evt[j],
        "lat": c.lat[j],
        "lon": c.lon[j],
        "speed_kmh": c.speed[j],
        "heading": c.heading[j],
        "odo_km": c.odometer[j],
        "ign": c.ignition[j],
        "batt_v": c.lv[j],
        "tpms_kpa": c.tyres[j],
        "dtc": c.dtcs[j],
        "schema": "orion.v1",
    }
    if c.has_engine[j]:
        d["rpm"] = c.rpm[j]
        d["coolant_c"] = c.coolant[j]
        d["fuel_pct"] = c.fuel[j]
    if c.has_hv[j]:
        d["soc_pct"] = c.soc[j]
        d["soh_pct"] = c.soh[j]
        d["pack_temp_c"] = c.pack_temp[j]
        d["cell_delta_mv"] = c.cell_delta[j]
    return d


def encode_vega(c: Columns, j: int) -> dict[str, Any]:
    fl, fr, rl, rr = c.tyres[j]
    d: dict[str, Any] = {
        "header": {
            "vehicleIdentifier": c.vin[j],
            "timestamp": iso_ist(c.ts[j]),
            "sequence": c.seq[j],
            "eventType": c.evt[j].lower(),
        },
        "location": {"latitude": c.lat[j], "longitude": c.lon[j], "headingDeg": c.heading[j]},
        "motion": {
            "speedMph": round(c.speed[j] * 0.621371, 1),
            "odometerMiles": round(c.odometer[j] * 0.621371, 2),
        },
        "electrical": {"batteryVolts": c.lv[j]},
        "tires": {
            "frontLeftPsi": round(fl * 0.145038, 1),
            "frontRightPsi": round(fr * 0.145038, 1),
            "rearLeftPsi": round(rl * 0.145038, 1),
            "rearRightPsi": round(rr * 0.145038, 1),
        },
        "diagnostics": {"activeCodes": ",".join(c.dtcs[j])},
        "version": "2.3",
    }
    if c.has_engine[j]:
        d["engine"] = {
            "ignition": "ON" if c.ignition[j] else "OFF",
            "rpm": c.rpm[j],
            "coolantTempF": round(c.coolant[j] * 1.8 + 32.0, 1),
            "fuelLevelPct": c.fuel[j],
        }
    if c.has_hv[j]:
        d["hybrid"] = {
            "socPct": c.soc[j],
            "sohPct": c.soh[j],
            "packTempF": round(c.pack_temp[j] * 1.8 + 32.0, 1),
            "cellDeltaMv": c.cell_delta[j],
        }
    return d


def encode_lyra(c: Columns, j: int) -> dict[str, Any]:
    fl, fr, rl, rr = c.tyres[j]
    return {
        "vin": c.vin[j],
        "timestampMs": int(c.ts[j] * 1000),
        "counter": c.seq[j],
        "event": c.evt[j],
        "signals": [
            {"name": "position.lat", "value": c.lat[j], "unit": "deg"},
            {"name": "position.lon", "value": c.lon[j], "unit": "deg"},
            {"name": "vehicle.speed", "value": c.speed[j], "unit": "km/h"},
            {"name": "vehicle.heading", "value": c.heading[j], "unit": "deg"},
            {"name": "vehicle.odometer", "value": c.odometer[j], "unit": "km"},
            {"name": "vehicle.ready", "value": c.ignition[j], "unit": "bool"},
            {"name": "lv.voltage", "value": c.lv[j], "unit": "V"},
            {"name": "hv.soc", "value": c.soc[j], "unit": "%"},
            {"name": "hv.soh", "value": c.soh[j], "unit": "%"},
            {"name": "hv.packTemp", "value": c.pack_temp[j], "unit": "degC"},
            {"name": "hv.cellDelta", "value": round(c.cell_delta[j] / 1000.0, 3), "unit": "V"},
            {"name": "tyre.fl.pressure", "value": round(fl / 100.0, 3), "unit": "bar"},
            {"name": "tyre.fr.pressure", "value": round(fr / 100.0, 3), "unit": "bar"},
            {"name": "tyre.rl.pressure", "value": round(rl / 100.0, 3), "unit": "bar"},
            {"name": "tyre.rr.pressure", "value": round(rr / 100.0, 3), "unit": "bar"},
        ],
        "faults": [{"code": code, "status": "active"} for code in c.dtcs[j]],
    }


ENCODERS = (encode_orion, encode_vega, encode_lyra)

# ------------------------------------------------------------------ missing fields
_ORION_DROPPABLE = (
    "lat",
    "lon",
    "speed_kmh",
    "odo_km",
    "batt_v",
    "tpms_kpa",
    "coolant_c",
    "ts",
    "vin",
    "seq",
)
_VEGA_SECTIONS = ("location", "motion", "electrical", "tires", "engine", "diagnostics")


def drop_field(oem: int, d: dict[str, Any], rng: np.random.Generator) -> str:
    """Remove one field in place; returns a label for the stats."""
    if oem == 0:
        keys = [k for k in _ORION_DROPPABLE if k in d]
        key = keys[int(rng.integers(len(keys)))]
        del d[key]
        return f"orion.{key}"
    if oem == 1:
        if rng.random() < 0.2:
            del d["header"]["timestamp"]
            return "vega.header.timestamp"
        sections = [k for k in _VEGA_SECTIONS if k in d]
        key = sections[int(rng.integers(len(sections)))]
        del d[key]
        return f"vega.{key}"
    if rng.random() < 0.2:
        del d["timestampMs"]
        return "lyra.timestampMs"
    removed = d["signals"].pop(int(rng.integers(len(d["signals"]))))
    return f"lyra.{removed['name']}"


# ------------------------------------------------------------------ malformed payloads
def corrupt(oem: int, d: dict[str, Any], kind: str, rng: np.random.Generator) -> tuple[bytes, str]:
    """Return (payload, oem_header) for a malformed event of the given kind."""
    oem_code = ("ORION", "VEGA", "LYRA")[oem]
    if kind == "truncated":
        raw = orjson.dumps(d)
        return raw[: max(1, len(raw) // 2)], oem_code
    if kind == "not_json":
        return b"<telemetry><status>OK</status><payload>\x00\xff</payload>", oem_code
    if kind == "unknown_oem":
        body = {"deviceId": f"ZT-{int(rng.integers(1_000_000)):06d}", "data": {"spd": 12.5}}
        return orjson.dumps(body), "ZETA"

    field_paths = {
        # kind: (orion path, vega path, lyra path/signal) -> bad value
        "bad_vin": (("vin",), ("header", "vehicleIdentifier"), ("vin",)),
        "out_of_range": (("lat",), ("location", "latitude"), ("signals", "position.lat")),
        "wrong_type": (("speed_kmh",), ("motion", "speedMph"), ("signals", "vehicle.speed")),
        "bad_timestamp": (("ts",), ("header", "timestamp"), ("timestampMs",)),
    }
    bad_values: dict[str, Any] = {
        "bad_vin": "IOQ00000000000000",
        "out_of_range": 999.0,
        "wrong_type": "fast",
        "bad_timestamp": "yesterday",
    }
    path = field_paths[kind][oem]
    value = bad_values[kind]
    if path[0] == "signals":
        for signal in d["signals"]:
            if signal["name"] == path[1]:
                signal["value"] = value
    elif len(path) == 2:
        d[path[0]][path[1]] = value
    else:
        d[path[0]] = value
    return orjson.dumps(d), oem_code

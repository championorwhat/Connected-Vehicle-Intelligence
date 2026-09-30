"""OEM adapters: map each OEM's wire format to canonical field names and SI units.

Adapter pattern + registry: onboarding a new OEM or a new schema version of an
existing one means registering one more function under (oem, schema); nothing
downstream changes. Adapters only translate shape and units; they raise
`Rejected` for payloads they cannot interpret. Semantic checks (ranges, VIN
policy, clock skew) live in `validation.py`, shared by all adapters.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from prognos_common.dtc import extract_dtcs

Canonical = dict[str, Any]
Adapter = Callable[[dict[str, Any]], Canonical]

MPH_TO_KMH = 1.0 / 0.621371
MILES_TO_KM = 1.0 / 0.621371
PSI_TO_KPA = 1.0 / 0.145038


class Rejected(Exception):
    """Payload cannot become a canonical event. `reason` is a stable DLQ reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def number(value: Any, field: str) -> float | int | None:
    """Numeric field or None; bools and strings are type errors, not numbers.

    Uses exact type checks (hot path: ~15 calls per event); `type(True) is bool`,
    so booleans are rejected without a separate isinstance test.
    """
    kind = type(value)
    if kind is float or kind is int or value is None:
        return value  # type: ignore[no-any-return]
    raise Rejected("invalid_type", field)


def whole(value: Any, field: str) -> int | None:
    """Numeric field delivered as int or float, canonicalised to int (rpm, heading, mV)."""
    kind = type(value)
    if kind is int or value is None:
        return value  # type: ignore[no-any-return]
    if kind is float:
        return int(round(value))  # noqa: RUF046 - value is Any; int() fixes the type
    raise Rejected("invalid_type", field)


def integer(value: Any, field: str) -> int | None:
    if type(value) is int or value is None:
        return value
    raise Rejected("invalid_type", field)


def iso_to_epoch(value: Any, field: str = "event_ts") -> float | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Rejected("invalid_timestamp", field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise Rejected("invalid_timestamp", field) from None
    if parsed.tzinfo is None:
        raise Rejected("invalid_timestamp", f"{field} has no timezone")
    return parsed.timestamp()


def _scaled(value: float | None, factor: float) -> float | None:
    return None if value is None else round(value * factor, 2)


def _f_to_c(value: float | None) -> float | None:
    return None if value is None else round((value - 32.0) / 1.8, 2)


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    section = payload.get(name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise Rejected("invalid_type", name)
    return section


# ------------------------------------------------------------------- ORION (flat, metric)
def orion_v1(p: dict[str, Any]) -> Canonical:
    tyres = p.get("tpms_kpa")
    if tyres is not None and (not isinstance(tyres, list) or len(tyres) != 4):
        raise Rejected("invalid_type", "tpms_kpa")
    fl, fr, rl, rr = tyres if tyres is not None else (None, None, None, None)
    dtc = p.get("dtc") or []
    if not isinstance(dtc, list):
        raise Rejected("invalid_type", "dtc")
    return {
        "vin": p.get("vin"),
        "event_ts": iso_to_epoch(p.get("ts")),
        "seq": integer(p.get("seq"), "seq"),
        "event_type": p.get("evt", "PERIODIC"),
        "latitude": number(p.get("lat"), "lat"),
        "longitude": number(p.get("lon"), "lon"),
        "speed_kmh": number(p.get("speed_kmh"), "speed_kmh"),
        "heading_deg": whole(p.get("heading"), "heading"),
        "odometer_km": number(p.get("odo_km"), "odo_km"),
        "ignition_on": p.get("ign"),
        "engine_rpm": whole(p.get("rpm"), "rpm"),
        "coolant_temp_c": number(p.get("coolant_c"), "coolant_c"),
        "fuel_level_pct": number(p.get("fuel_pct"), "fuel_pct"),
        "lv_battery_v": number(p.get("batt_v"), "batt_v"),
        "hv_soc_pct": number(p.get("soc_pct"), "soc_pct"),
        "hv_soh_pct": number(p.get("soh_pct"), "soh_pct"),
        "hv_pack_temp_c": number(p.get("pack_temp_c"), "pack_temp_c"),
        "hv_cell_delta_mv": whole(p.get("cell_delta_mv"), "cell_delta_mv"),
        "tyre_fl_kpa": number(fl, "tpms_kpa"),
        "tyre_fr_kpa": number(fr, "tpms_kpa"),
        "tyre_rl_kpa": number(rl, "tpms_kpa"),
        "tyre_rr_kpa": number(rr, "tpms_kpa"),
        "dtc_raw": [str(c) for c in dtc],
    }


# ------------------------------------------------------------------- VEGA (nested, imperial)
def vega_2_3(p: dict[str, Any]) -> Canonical:
    header = _section(p, "header")
    location = _section(p, "location")
    motion = _section(p, "motion")
    engine = _section(p, "engine")
    hybrid = _section(p, "hybrid")
    tires = _section(p, "tires")
    electrical = _section(p, "electrical")
    codes = _section(p, "diagnostics").get("activeCodes") or ""
    if not isinstance(codes, str):
        raise Rejected("invalid_type", "diagnostics.activeCodes")
    ignition = engine.get("ignition")
    return {
        "vin": header.get("vehicleIdentifier"),
        "event_ts": iso_to_epoch(header.get("timestamp")),
        "seq": integer(header.get("sequence"), "header.sequence"),
        "event_type": str(header.get("eventType", "periodic")).upper(),
        "latitude": number(location.get("latitude"), "location.latitude"),
        "longitude": number(location.get("longitude"), "location.longitude"),
        "speed_kmh": _scaled(number(motion.get("speedMph"), "motion.speedMph"), MPH_TO_KMH),
        "heading_deg": whole(location.get("headingDeg"), "location.headingDeg"),
        "odometer_km": _scaled(
            number(motion.get("odometerMiles"), "motion.odometerMiles"), MILES_TO_KM
        ),
        "ignition_on": None if ignition is None else ignition == "ON",
        "engine_rpm": whole(engine.get("rpm"), "engine.rpm"),
        "coolant_temp_c": _f_to_c(number(engine.get("coolantTempF"), "engine.coolantTempF")),
        "fuel_level_pct": number(engine.get("fuelLevelPct"), "engine.fuelLevelPct"),
        "lv_battery_v": number(electrical.get("batteryVolts"), "electrical.batteryVolts"),
        "hv_soc_pct": number(hybrid.get("socPct"), "hybrid.socPct"),
        "hv_soh_pct": number(hybrid.get("sohPct"), "hybrid.sohPct"),
        "hv_pack_temp_c": _f_to_c(number(hybrid.get("packTempF"), "hybrid.packTempF")),
        "hv_cell_delta_mv": whole(hybrid.get("cellDeltaMv"), "hybrid.cellDeltaMv"),
        "tyre_fl_kpa": _scaled(number(tires.get("frontLeftPsi"), "tires"), PSI_TO_KPA),
        "tyre_fr_kpa": _scaled(number(tires.get("frontRightPsi"), "tires"), PSI_TO_KPA),
        "tyre_rl_kpa": _scaled(number(tires.get("rearLeftPsi"), "tires"), PSI_TO_KPA),
        "tyre_rr_kpa": _scaled(number(tires.get("rearRightPsi"), "tires"), PSI_TO_KPA),
        "dtc_raw": [d.code for d in extract_dtcs(codes)],
    }


# ------------------------------------------------------------------- LYRA (signal list)
# signal name -> (canonical field, {unit: factor to canonical unit})
_LYRA_SIGNALS: dict[str, tuple[str, dict[str, float]]] = {
    "position.lat": ("latitude", {"deg": 1.0}),
    "position.lon": ("longitude", {"deg": 1.0}),
    "vehicle.speed": ("speed_kmh", {"km/h": 1.0, "m/s": 3.6}),
    "vehicle.heading": ("heading_deg", {"deg": 1.0}),
    "vehicle.odometer": ("odometer_km", {"km": 1.0, "m": 0.001}),
    "lv.voltage": ("lv_battery_v", {"V": 1.0}),
    "hv.soc": ("hv_soc_pct", {"%": 1.0}),
    "hv.soh": ("hv_soh_pct", {"%": 1.0}),
    "hv.packTemp": ("hv_pack_temp_c", {"degC": 1.0}),
    "hv.cellDelta": ("hv_cell_delta_mv", {"V": 1000.0, "mV": 1.0}),
    "tyre.fl.pressure": ("tyre_fl_kpa", {"bar": 100.0, "kPa": 1.0}),
    "tyre.fr.pressure": ("tyre_fr_kpa", {"bar": 100.0, "kPa": 1.0}),
    "tyre.rl.pressure": ("tyre_rl_kpa", {"bar": 100.0, "kPa": 1.0}),
    "tyre.rr.pressure": ("tyre_rr_kpa", {"bar": 100.0, "kPa": 1.0}),
}


def lyra_v1(p: dict[str, Any]) -> Canonical:
    signals = p.get("signals")
    if not isinstance(signals, list):
        raise Rejected("invalid_type", "signals")
    out: Canonical = {
        "vin": p.get("vin"),
        "seq": integer(p.get("counter"), "counter"),
        "event_type": p.get("event", "PERIODIC"),
        "ignition_on": None,
        "engine_rpm": None,
        "coolant_temp_c": None,
        "fuel_level_pct": None,
    }
    ts_ms = p.get("timestampMs")
    if ts_ms is not None and (isinstance(ts_ms, bool) or not isinstance(ts_ms, int)):
        raise Rejected("invalid_timestamp", "timestampMs")
    out["event_ts"] = None if ts_ms is None else ts_ms / 1000.0
    for field, _ in _LYRA_SIGNALS.values():
        out[field] = None
    for signal in signals:
        if not isinstance(signal, dict):
            raise Rejected("invalid_type", "signals[]")
        name = signal.get("name")
        if name == "vehicle.ready":
            out["ignition_on"] = bool(signal.get("value"))
            continue
        mapping = _LYRA_SIGNALS.get(str(name))
        if mapping is None:
            continue  # unknown signals are ignored (forward compatible)
        field, units = mapping
        factor = units.get(str(signal.get("unit")))
        if factor is None:
            raise Rejected("unsupported_unit", f"{name}:{signal.get('unit')}")
        value = number(signal.get("value"), str(name))
        if value is None or (factor == 1.0 and field not in _WHOLE_FIELDS):
            out[field] = value
        elif field in _WHOLE_FIELDS:
            out[field] = round(value * factor)
        else:
            out[field] = round(value * factor, 2)
    faults = p.get("faults") or []
    if not isinstance(faults, list):
        raise Rejected("invalid_type", "faults")
    out["dtc_raw"] = [str(f.get("code")) for f in faults if isinstance(f, dict)]
    return out


_WHOLE_FIELDS = frozenset({"engine_rpm", "heading_deg", "hv_cell_delta_mv"})

ADAPTERS: dict[tuple[str, str], Adapter] = {
    ("ORION", "orion.v1"): orion_v1,
    ("VEGA", "vega.2.3"): vega_2_3,
    ("LYRA", "lyra.v1"): lyra_v1,
}
DEFAULT_SCHEMA = {"ORION": "orion.v1", "VEGA": "vega.2.3", "LYRA": "lyra.v1"}


def resolve(oem: str | None, schema: str | None) -> Adapter:
    if oem is None or oem not in DEFAULT_SCHEMA:
        raise Rejected("unknown_oem", str(oem))
    adapter = ADAPTERS.get((oem, schema or DEFAULT_SCHEMA[oem]))
    if adapter is None:
        raise Rejected("unknown_schema_version", f"{oem}/{schema}")
    return adapter

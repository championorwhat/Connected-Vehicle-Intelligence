"""Reference catalog: OEMs, vehicle models, failure modes, DTCs, cost parameters.

Single source of truth shared by the database seed and the simulator, so the
simulator can never emit a model or fault that the database does not know.

All OEMs and models are fictional. WMIs are synthetic; any overlap with a real
manufacturer allocation is coincidental.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Powertrain(StrEnum):
    ICE = "ICE"
    HEV = "HEV"
    BEV = "BEV"


class PayloadFormat(StrEnum):
    FLAT_JSON_METRIC = "flat_json_metric"  # flat keys, SI units, ISO-8601 timestamps
    NESTED_JSON_IMPERIAL = "nested_json_imperial"  # nested objects, mph / psi / °F
    SIGNAL_LIST_JSON = "signal_list_json"  # [{"name","value","unit"}], epoch-ms timestamps


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Oem:
    code: str
    display_name: str
    payload_format: PayloadFormat
    vin_wmi: str
    requires_vin_check_digit: bool


@dataclass(frozen=True, slots=True)
class VehicleModel:
    oem_code: str
    model_code: str  # doubles as the VIN descriptor section (positions 4-8)
    display_name: str
    powertrain: Powertrain
    vehicle_class: str
    firmware_versions: tuple[str, ...]
    fleet_share: float  # relative frequency in generated fleets


@dataclass(frozen=True, slots=True)
class FailureMode:
    code: str
    display_name: str
    component: str
    powertrains: frozenset[Powertrain]


@dataclass(frozen=True, slots=True)
class DtcDefinition:
    code: str
    description: str
    severity: Severity
    failure_mode: str | None
    is_synthetic: bool  # True = invented manufacturer-specific code, not an SAE generic


@dataclass(frozen=True, slots=True)
class CostParameters:
    planned_repair_cost: float
    unplanned_repair_cost: float
    downtime_cost_per_day: float
    extra_downtime_days: float
    currency: str
    source: str


OEMS: tuple[Oem, ...] = (
    Oem("ORION", "Orion Motors", PayloadFormat.FLAT_JSON_METRIC, "PG1", True),
    Oem("VEGA", "Vega Automotive", PayloadFormat.NESTED_JSON_IMPERIAL, "PG2", True),
    Oem("LYRA", "Lyra Electric", PayloadFormat.SIGNAL_LIST_JSON, "PG3", False),
)

VEHICLE_MODELS: tuple[VehicleModel, ...] = (
    VehicleModel(
        "ORION", "CT1A5", "Orion Cargo Van", Powertrain.ICE, "van", ("3.1.0", "3.2.4"), 0.18
    ),
    VehicleModel("ORION", "HL2B7", "Orion Hauler", Powertrain.ICE, "light_truck", ("2.8.1",), 0.14),
    VehicleModel("ORION", "SD3H1", "Orion Sedan Hybrid", Powertrain.HEV, "car", ("5.0.2",), 0.05),
    VehicleModel("VEGA", "TR4D2", "Vega Transit", Powertrain.ICE, "truck", ("11.4", "11.6"), 0.16),
    VehicleModel("VEGA", "PK5C8", "Vega Pickup", Powertrain.ICE, "light_truck", ("9.2",), 0.17),
    VehicleModel("VEGA", "CV6H3", "Vega Crossover Hybrid", Powertrain.HEV, "car", ("7.7",), 0.05),
    VehicleModel("LYRA", "EV7V4", "Lyra e-Van", Powertrain.BEV, "van", ("2026.3", "2026.7"), 0.12),
    VehicleModel("LYRA", "EC8S6", "Lyra e-Compact", Powertrain.BEV, "car", ("2026.3",), 0.08),
    VehicleModel("LYRA", "ET9T5", "Lyra e-Truck", Powertrain.BEV, "truck", ("2026.5",), 0.05),
)

_COMBUSTION = frozenset({Powertrain.ICE, Powertrain.HEV})
_ALL = frozenset(Powertrain)

FAILURE_MODES: tuple[FailureMode, ...] = (
    FailureMode("COOLING_FAILURE", "Cooling system failure", "engine cooling", _COMBUSTION),
    FailureMode("IGNITION_MISFIRE", "Ignition misfire", "ignition", _COMBUSTION),
    FailureMode("LV_BATTERY_FAILURE", "12 V battery / starter failure", "12 V battery", _ALL),
    FailureMode("TYRE_SLOW_LEAK", "Tyre slow leak", "tyres", _ALL),
    FailureMode(
        "HV_BATTERY_THERMAL",
        "HV battery thermal / cell imbalance",
        "traction battery",
        frozenset({Powertrain.HEV, Powertrain.BEV}),
    ),
)

# SAE J2012 generic codes use their standard descriptions. Tyre-pressure codes are
# manufacturer-specific in practice, so we define synthetic C1xxx codes and flag them.
DTCS: tuple[DtcDefinition, ...] = (
    DtcDefinition("P0217", "Engine coolant over temperature condition", Severity.CRITICAL, "COOLING_FAILURE", False),
    DtcDefinition("P0118", "Engine coolant temperature circuit high", Severity.WARNING, "COOLING_FAILURE", False),
    DtcDefinition("P0128", "Coolant thermostat (coolant temperature below regulating temperature)", Severity.WARNING, "COOLING_FAILURE", False),
    DtcDefinition("P0300", "Random/multiple cylinder misfire detected", Severity.CRITICAL, "IGNITION_MISFIRE", False),
    DtcDefinition("P0301", "Cylinder 1 misfire detected", Severity.WARNING, "IGNITION_MISFIRE", False),
    DtcDefinition("P0302", "Cylinder 2 misfire detected", Severity.WARNING, "IGNITION_MISFIRE", False),
    DtcDefinition("P0303", "Cylinder 3 misfire detected", Severity.WARNING, "IGNITION_MISFIRE", False),
    DtcDefinition("P0304", "Cylinder 4 misfire detected", Severity.WARNING, "IGNITION_MISFIRE", False),
    DtcDefinition("P0562", "System voltage low", Severity.WARNING, "LV_BATTERY_FAILURE", False),
    DtcDefinition("P0563", "System voltage high", Severity.INFO, "LV_BATTERY_FAILURE", False),
    DtcDefinition("C1A10", "Tyre pressure low - front left (synthetic OEM code)", Severity.WARNING, "TYRE_SLOW_LEAK", True),
    DtcDefinition("C1A11", "Tyre pressure low - front right (synthetic OEM code)", Severity.WARNING, "TYRE_SLOW_LEAK", True),
    DtcDefinition("C1A12", "Tyre pressure low - rear left (synthetic OEM code)", Severity.WARNING, "TYRE_SLOW_LEAK", True),
    DtcDefinition("C1A13", "Tyre pressure low - rear right (synthetic OEM code)", Severity.WARNING, "TYRE_SLOW_LEAK", True),
    DtcDefinition("P0A80", "Replace hybrid/EV battery pack", Severity.CRITICAL, "HV_BATTERY_THERMAL", False),
    DtcDefinition("P0A7F", "Hybrid/EV battery pack deterioration", Severity.WARNING, "HV_BATTERY_THERMAL", False),
    DtcDefinition("P0AFA", "Hybrid/EV battery system voltage low", Severity.WARNING, "HV_BATTERY_THERMAL", False),
    # Background noise: real-world codes unrelated to the modelled failure modes.
    DtcDefinition("P0420", "Catalyst system efficiency below threshold (bank 1)", Severity.INFO, None, False),
    DtcDefinition("P0455", "Evaporative emission system leak detected (large leak)", Severity.INFO, None, False),
    DtcDefinition("U0100", "Lost communication with ECM/PCM 'A'", Severity.WARNING, None, False),
)  # fmt: skip

# PLACEHOLDER cost inputs. They are NOT sourced figures: they exist so the pipeline
# runs end to end, and every row carries source="PLACEHOLDER ..." so the API and UI
# can label derived money values as unverified. Replaced with cited values in M17.
_PLACEHOLDER = "PLACEHOLDER - not yet sourced; replace with cited values (M17)"
DEFAULT_COSTS: dict[str, CostParameters] = {
    fm.code: CostParameters(0.0, 0.0, 0.0, 0.0, "INR", _PLACEHOLDER) for fm in FAILURE_MODES
}

OEM_BY_CODE = {o.code: o for o in OEMS}
MODEL_BY_CODE = {m.model_code: m for m in VEHICLE_MODELS}
FAILURE_MODE_BY_CODE = {f.code: f for f in FAILURE_MODES}
DTC_BY_CODE = {d.code: d for d in DTCS}

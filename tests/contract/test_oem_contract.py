"""Contract: what the simulator's OEM encoders emit, the normaliser's adapters understand.

Known metric values go through each real OEM encoder (unit conversions, nesting,
timestamps), then through the matching adapter; they must come back equal
within the rounding each wire format applies.
"""

from __future__ import annotations

import orjson
import pytest

from prognos_common.roster import generate_roster
from prognos_sim.formats import ENCODERS, Columns
from prognos_stream.processor import CANONICAL, Normalizer
from prognos_stream.registry import VehicleRegistry

ROSTER = generate_roster(300, 3, 42)
TS = 1_790_000_000.25
TOLERANCE = {  # absolute tolerance per canonical field, from each format's rounding
    "speed_kmh": 0.2, "odometer_km": 0.05, "coolant_temp_c": 0.1, "hv_pack_temp_c": 0.1,
    "tyre_fl_kpa": 0.7, "tyre_fr_kpa": 0.7, "tyre_rl_kpa": 0.7, "tyre_rr_kpa": 0.7,
}  # fmt: skip


def columns(vin: str, has_engine: bool, has_hv: bool) -> Columns:
    return Columns(
        vin=[vin], seq=[41], ts=[TS], evt=["HARSH_BRAKE"], lat=[13.08271], lon=[80.270712],
        speed=[64.3], heading=[270], odometer=[18234.71], ignition=[True], rpm=[2150],
        coolant=[97.4], fuel=[55.5], lv=[14.08], tyres=[[241.3, 238.9, 240.2, 180.4]],
        soc=[61.2], soh=[93.4], pack_temp=[31.7], cell_delta=[46],
        dtcs=[["P0301", "P0420"]], has_engine=[has_engine], has_hv=[has_hv],
    )  # fmt: skip


EXPECTED = {
    "latitude": 13.08271, "longitude": 80.270712, "speed_kmh": 64.3, "heading_deg": 270,
    "odometer_km": 18234.71, "tyre_fl_kpa": 241.3, "tyre_rr_kpa": 180.4, "seq": 41,
    "event_type": "HARSH_BRAKE", "event_ts": "2026-09-21T14:13:20.250Z",
}  # fmt: skip


@pytest.mark.parametrize(("oem_index", "oem"), [(0, "ORION"), (1, "VEGA"), (2, "LYRA")])
def test_encoder_adapter_round_trip(oem_index: int, oem: str) -> None:
    vehicle = next(v for v in ROSTER.vehicles if v.oem_code == oem)
    has_engine = oem != "LYRA"
    has_hv = oem == "LYRA"
    payload = ENCODERS[oem_index](columns(vehicle.vin, has_engine, has_hv), 0)
    normalizer = Normalizer(VehicleRegistry.from_roster(ROSTER))
    schema = {"ORION": b"orion.v1", "VEGA": b"vega.2.3", "LYRA": b"lyra.v1"}[oem]
    outcome = normalizer.process(
        orjson.dumps(payload), {"oem": oem.encode(), "schema": schema}, 0, TS + 1
    )
    assert outcome.kind == CANONICAL, outcome
    event = orjson.loads(outcome.value)
    assert event["vehicle_id"] == str(vehicle.vehicle_id)
    assert event["tenant_id"] == str(vehicle.tenant_id)
    for field, expected in EXPECTED.items():
        if isinstance(expected, float):
            assert event[field] == pytest.approx(expected, abs=TOLERANCE.get(field, 1e-6)), field
        else:
            assert event[field] == expected, field
    if has_engine:
        assert event["engine_rpm"] == 2150
        assert event["coolant_temp_c"] == pytest.approx(97.4, abs=0.1)
        assert event["ignition_on"] is True
    else:
        assert event["engine_rpm"] is None
    if has_hv:
        assert event["hv_cell_delta_mv"] == 46
        assert event["hv_pack_temp_c"] == pytest.approx(31.7, abs=0.1)
    assert event["dtc_codes"] == ["P0301", "P0420"]

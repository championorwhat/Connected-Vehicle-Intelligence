from collections import Counter

import pytest

from prognos_common.catalog import (
    DTCS,
    FAILURE_MODE_BY_CODE,
    MODEL_BY_CODE,
    OEM_BY_CODE,
    VEHICLE_MODELS,
)
from prognos_common.dtc import is_valid_dtc
from prognos_common.roster import generate_roster
from prognos_common.vin import VIN_PATTERN, is_valid_vin


def test_catalog_is_internally_consistent() -> None:
    for model in VEHICLE_MODELS:
        assert model.oem_code in OEM_BY_CODE
        assert VIN_PATTERN.fullmatch(f"AAA{model.model_code}0AA000000".ljust(17, "0")[:17])
    for dtc in DTCS:
        assert is_valid_dtc(dtc.code)
        assert dtc.failure_mode is None or dtc.failure_mode in FAILURE_MODE_BY_CODE
    assert abs(sum(m.fleet_share for m in VEHICLE_MODELS) - 1.0) < 1e-9


@pytest.fixture(scope="module")
def roster():  # type: ignore[no-untyped-def]
    return generate_roster(vehicle_count=5_000, tenant_count=20, seed=7)


def test_roster_is_deterministic(roster) -> None:  # type: ignore[no-untyped-def]
    again = generate_roster(vehicle_count=5_000, tenant_count=20, seed=7)
    assert again == roster
    assert generate_roster(vehicle_count=5_000, tenant_count=20, seed=8) != roster


def test_roster_counts_and_uniqueness(roster) -> None:  # type: ignore[no-untyped-def]
    assert len(roster.vehicles) == 5_000
    assert len(roster.tenants) == 20
    assert len({v.vin for v in roster.vehicles}) == 5_000
    assert len({v.vehicle_id for v in roster.vehicles}) == 5_000


def test_tenant_sizes_are_skewed(roster) -> None:  # type: ignore[no-untyped-def]
    sizes = sorted(Counter(v.tenant_id for v in roster.vehicles).values(), reverse=True)
    assert min(sizes) >= 1
    assert sizes[0] > 3 * sizes[-1]


def test_vehicles_reference_their_own_tenant(roster) -> None:  # type: ignore[no-untyped-def]
    fleet_tenant = {f.fleet_id: f.tenant_id for f in roster.fleets}
    shop_tenant = {w.workshop_id: w.tenant_id for w in roster.workshops}
    for v in roster.vehicles:
        assert fleet_tenant[v.fleet_id] == v.tenant_id
        assert shop_tenant[v.home_workshop_id] == v.tenant_id


def test_vins_respect_oem_check_digit_policy(roster) -> None:  # type: ignore[no-untyped-def]
    for v in roster.vehicles:
        oem = OEM_BY_CODE[v.oem_code]
        assert is_valid_vin(v.vin, require_check_digit=oem.requires_vin_check_digit)
        assert MODEL_BY_CODE[v.model_code].oem_code == v.oem_code
    lyra = [v for v in roster.vehicles if v.oem_code == "LYRA"]
    assert any(not is_valid_vin(v.vin) for v in lyra), "some LYRA VINs should skip check digit"


def test_drivers_are_pseudonymous(roster) -> None:  # type: ignore[no-untyped-def]
    assert all(d.pseudonym.startswith("DRV-") for d in roster.drivers)
    assert 0.85 < len(roster.drivers) / len(roster.vehicles) < 0.95


def test_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        generate_roster(vehicle_count=5, tenant_count=20)
    with pytest.raises(ValueError):
        generate_roster(vehicle_count=100, tenant_count=21)

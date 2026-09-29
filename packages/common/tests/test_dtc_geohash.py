import pytest

from prognos_common import geohash
from prognos_common.dtc import extract_dtcs, is_valid_dtc


@pytest.mark.parametrize("code", ["P0301", "C1A10", "B0001", "U0100", "P0A80"])
def test_valid_dtcs(code: str) -> None:
    assert is_valid_dtc(code)


@pytest.mark.parametrize("code", ["P4301", "X0301", "P030", "P03011", "p0301", "P0G01"])
def test_invalid_dtcs(code: str) -> None:
    assert not is_valid_dtc(code)


def test_extract_dtcs_from_noisy_oem_text() -> None:
    found = extract_dtcs("codes: p0301,P0420; P0301 AP03011 C1A10")
    assert [d.code for d in found] == ["P0301", "P0420", "C1A10"]
    assert found[0].system == "powertrain"
    assert found[0].is_generic
    assert found[2].system == "chassis"
    assert not found[2].is_generic


def test_geohash_reference_value() -> None:
    # Reference example from the original geohash description.
    assert geohash.encode(57.64911, 10.40744, 11) == "u4pruydqqvj"


def test_geohash_decode_is_within_cell() -> None:
    lat, lon = geohash.decode(geohash.encode(13.0827, 80.2707, 7))
    assert abs(lat - 13.0827) < 0.001
    assert abs(lon - 80.2707) < 0.001


def test_geohash_prefix_property_for_masking() -> None:
    precise = geohash.encode(12.9716, 77.5946, 9)
    assert precise.startswith(geohash.encode(12.9716, 77.5946, 5))


@pytest.mark.parametrize(("lat", "lon", "precision"), [(91, 0, 5), (0, 181, 5), (0, 0, 13)])
def test_geohash_rejects_bad_input(lat: float, lon: float, precision: int) -> None:
    with pytest.raises(ValueError):
        geohash.encode(lat, lon, precision)

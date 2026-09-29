import pytest

from prognos_common.vin import (
    InvalidVinError,
    build_vin,
    compute_check_digit,
    is_valid_vin,
    model_year_code,
)


@pytest.mark.parametrize(
    "vin",
    [
        "1M8GDM9AXKP042788",  # textbook example, check digit 'X'
        "11111111111111111",  # all-ones is famously self-consistent
        "1HGCM82633A004352",  # example VIN from the hackathon brief
    ],
)
def test_known_valid_vins(vin: str) -> None:
    assert is_valid_vin(vin)


def test_wrong_check_digit_rejected_only_when_required() -> None:
    bad = "1M8GDM9A1KP042788"
    assert not is_valid_vin(bad)
    assert is_valid_vin(bad, require_check_digit=False)


@pytest.mark.parametrize(
    "vin",
    ["1M8GDM9AXKP04278", "1M8GDM9AXKP0427888", "1M8GDM9AXKO042788", "1m8gdm9axkp042788", ""],
)
def test_structurally_invalid(vin: str) -> None:
    assert not is_valid_vin(vin, require_check_digit=False)


def test_check_digit_rejects_illegal_characters() -> None:
    with pytest.raises(InvalidVinError):
        compute_check_digit("1M8GDM9AXKQ042788")


def test_build_vin_round_trips() -> None:
    vin = build_vin("PG1", "CT1A5", 2024, "C", 123)
    assert len(vin) == 17
    assert vin[9] == "R"  # 2024
    assert vin.endswith("000123")
    assert is_valid_vin(vin)


@pytest.mark.parametrize(("year", "code"), [(2010, "A"), (2018, "J"), (2026, "T"), (2031, "1")])
def test_model_year_codes(year: int, code: str) -> None:
    assert model_year_code(year) == code


def test_model_year_out_of_cycle() -> None:
    with pytest.raises(ValueError):
        model_year_code(2009)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("PG", "CT1A5", 2024, "C", 1), "wmi"),
        (("PG1", "CT1A5", 2024, "C", 1_000_000), "serial"),
        (("PGI", "CT1A5", 2024, "C", 1), "illegal"),
    ],
)
def test_build_vin_validates_parts(args: tuple[str, str, int, str, int], message: str) -> None:
    with pytest.raises(InvalidVinError, match=message):
        build_vin(*args)

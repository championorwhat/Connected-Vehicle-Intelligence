"""Vehicle Identification Number (VIN) validation and construction.

A VIN is 17 characters from [A-Z0-9] excluding I, O and Q (ISO 3779).
Position 9 is a check digit in North America (49 CFR 565); many other
markets do not enforce it, so enforcement is a per-OEM policy.

Check digit algorithm (O(1): fixed 17 characters):
    total = sum(transliterate(c_i) * WEIGHTS[i] for i in 0..16)
    check = total mod 11, rendered as 'X' when 10
"""

from __future__ import annotations

import re

VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

_TRANSLITERATION: dict[str, int] = {
    **{str(d): d for d in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
_WEIGHTS: tuple[int, ...] = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)

# Position 10 model-year codes for the 2010-2039 cycle (letters skip I, O, Q, U, Z and 0).
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"
_YEAR_BASE = 2010


class InvalidVinError(ValueError):
    """Raised when a VIN is structurally invalid."""


def compute_check_digit(vin: str) -> str:
    """Return the check digit for a 17-char VIN (the value at index 8 is ignored)."""
    if len(vin) != 17:
        raise InvalidVinError(f"VIN must be 17 characters, got {len(vin)}")
    try:
        total = sum(_TRANSLITERATION[c] * w for c, w in zip(vin, _WEIGHTS, strict=True))
    except KeyError as exc:
        raise InvalidVinError(f"illegal VIN character {exc.args[0]!r}") from None
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def is_valid_vin(vin: str, *, require_check_digit: bool = True) -> bool:
    """Structural validation, plus the check digit when the OEM policy requires it."""
    if not VIN_PATTERN.fullmatch(vin):
        return False
    return not require_check_digit or vin[8] == compute_check_digit(vin)


def model_year_code(year: int) -> str:
    """VIN position-10 code for a model year in 2010..2039."""
    offset = year - _YEAR_BASE
    if not 0 <= offset < len(_YEAR_CODES):
        raise ValueError(f"model year {year} outside supported cycle 2010-2039")
    return _YEAR_CODES[offset]


def build_vin(wmi: str, vds: str, model_year: int, plant: str, serial: int) -> str:
    """Assemble a VIN with a correct check digit.

    wmi: 3-char manufacturer id, vds: 5-char descriptor, plant: 1 char,
    serial: 0..999999 production number.
    """
    if len(wmi) != 3 or len(vds) != 5 or len(plant) != 1:
        raise InvalidVinError("wmi/vds/plant must be 3/5/1 characters")
    if not 0 <= serial <= 999_999:
        raise InvalidVinError("serial must fit in 6 digits")
    draft = f"{wmi}{vds}0{model_year_code(model_year)}{plant}{serial:06d}"
    if not VIN_PATTERN.fullmatch(draft):
        raise InvalidVinError(f"illegal characters in VIN parts: {draft}")
    return draft[:8] + compute_check_digit(draft) + draft[9:]

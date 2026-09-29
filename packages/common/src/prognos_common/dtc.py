"""OBD-II Diagnostic Trouble Code (DTC) parsing (SAE J2012 format).

Format: one system letter (P powertrain, C chassis, B body, U network),
then a digit 0-3 (0/2 = SAE generic, 1/3 = manufacturer specific),
then three hex digits.  Example: P0301 = cylinder 1 misfire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DTC_PATTERN = re.compile(r"^[PCBU][0-3][0-9A-F]{3}$")
# Finds DTCs inside free-form OEM payload strings such as "codes: p0301,P0420;".
_DTC_SEARCH = re.compile(r"(?<![0-9A-Z])([PCBU][0-3][0-9A-F]{3})(?![0-9A-Z])", re.IGNORECASE)

_SYSTEMS = {"P": "powertrain", "C": "chassis", "B": "body", "U": "network"}


@dataclass(frozen=True, slots=True)
class Dtc:
    code: str

    @property
    def system(self) -> str:
        return _SYSTEMS[self.code[0]]

    @property
    def is_generic(self) -> bool:
        return self.code[1] in "02"


def is_valid_dtc(code: str) -> bool:
    return bool(DTC_PATTERN.fullmatch(code))


def extract_dtcs(raw: str) -> list[Dtc]:
    """Extract unique, upper-cased DTCs from raw text, preserving first-seen order."""
    seen: dict[str, None] = {}
    for match in _DTC_SEARCH.finditer(raw):
        seen.setdefault(match.group(1).upper(), None)
    return [Dtc(code) for code in seen]

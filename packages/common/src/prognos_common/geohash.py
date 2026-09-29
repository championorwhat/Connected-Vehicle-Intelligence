"""Geohash encoding (base32, interleaved longitude/latitude bisection).

Used for (a) location masking: truncating a geohash coarsens precision
(precision 5 ~ 4.9 km cell, 7 ~ 153 m), and (b) bucketing vehicles for map
clustering and nearest-workshop candidate search.

encode: O(p) time for precision p, O(p) space.
"""

from __future__ import annotations

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_DECODE = {c: i for i, c in enumerate(_BASE32)}


def encode(latitude: float, longitude: float, precision: int = 9) -> str:
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("latitude/longitude out of range")
    if not 1 <= precision <= 12:
        raise ValueError("precision must be 1..12")
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    chars: list[str] = []
    bits, bit_count, even = 0, 0, True
    while len(chars) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if longitude >= mid:
                bits, lon_lo = (bits << 1) | 1, mid
            else:
                bits, lon_hi = bits << 1, mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if latitude >= mid:
                bits, lat_lo = (bits << 1) | 1, mid
            else:
                bits, lat_hi = bits << 1, mid
        even = not even
        bit_count += 1
        if bit_count == 5:
            chars.append(_BASE32[bits])
            bits, bit_count = 0, 0
    return "".join(chars)


def decode(geohash: str) -> tuple[float, float]:
    """Return the (latitude, longitude) centre of the geohash cell."""
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    even = True
    for char in geohash:
        value = _DECODE[char]
        for shift in range(4, -1, -1):
            bit = (value >> shift) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                lon_lo, lon_hi = (mid, lon_hi) if bit else (lon_lo, mid)
            else:
                mid = (lat_lo + lat_hi) / 2
                lat_lo, lat_hi = (mid, lat_hi) if bit else (lat_lo, mid)
            even = not even
    return (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2

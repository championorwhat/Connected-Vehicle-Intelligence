"""Deterministic synthetic fleet roster.

`generate_roster(vehicle_count, tenant_count, seed)` always returns the same
tenants, fleets, workshops, vehicles and drivers for the same inputs. The
database seed and the simulator both call it, so they agree on every VIN and
vehicle_id without having to share a file.

Tenant sizes follow a Zipf-like distribution (a few large customers, many
small ones), which keeps partition-skew problems visible in testing.

Complexity: O(V + T) time and memory for V vehicles and T tenants.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from prognos_common.catalog import OEM_BY_CODE, VEHICLE_MODELS, Powertrain
from prognos_common.vin import build_vin

CITIES: tuple[tuple[str, float, float], ...] = (
    ("Chennai", 13.0827, 80.2707),
    ("Bengaluru", 12.9716, 77.5946),
    ("Mumbai", 19.0760, 72.8777),
    ("Delhi", 28.6139, 77.2090),
    ("Hyderabad", 17.3850, 78.4867),
    ("Pune", 18.5204, 73.8567),
    ("Kolkata", 22.5726, 88.3639),
    ("Ahmedabad", 23.0225, 72.5714),
)

_TENANT_WORDS = (
    ("Blue", "Harbor"), ("Swift", "Axle"), ("Granite", "Route"), ("Monsoon", "Freight"),
    ("Silver", "Lane"), ("Saffron", "Haul"), ("Coral", "Transit"), ("Iron", "Bridge"),
    ("Amber", "Wheel"), ("Cedar", "Cargo"), ("Delta", "Mile"), ("Ember", "Logistics"),
    ("Falcon", "Fleet"), ("Harbor", "Point"), ("Indigo", "Road"), ("Jade", "Movers"),
    ("Kite", "Express"), ("Lotus", "Carriers"), ("Maple", "Line"), ("Nimbus", "Mobility"),
)  # fmt: skip

_CHECK_DIGIT_OFF_CHARS = "0123456789ABCDEFGHJKLMNPRSTUVWXYZ"


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: uuid.UUID
    slug: str
    display_name: str


@dataclass(frozen=True, slots=True)
class Fleet:
    fleet_id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    base_city: str


@dataclass(frozen=True, slots=True)
class Workshop:
    workshop_id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    city: str
    latitude: float
    longitude: float
    daily_capacity: int


@dataclass(frozen=True, slots=True)
class Vehicle:
    vehicle_id: uuid.UUID
    tenant_id: uuid.UUID
    fleet_id: uuid.UUID
    vin: str
    oem_code: str
    model_code: str
    powertrain: Powertrain
    model_year: int
    firmware_version: str
    commissioned_on: date
    home_workshop_id: uuid.UUID
    home_latitude: float
    home_longitude: float


@dataclass(frozen=True, slots=True)
class Driver:
    driver_id: uuid.UUID
    tenant_id: uuid.UUID
    pseudonym: str
    assigned_vehicle_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Roster:
    tenants: tuple[Tenant, ...]
    fleets: tuple[Fleet, ...]
    workshops: tuple[Workshop, ...]
    vehicles: tuple[Vehicle, ...]
    drivers: tuple[Driver, ...]


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _tenant_sizes(vehicle_count: int, tenant_count: int) -> list[int]:
    """Zipf-like split (exponent 0.8) with largest-remainder rounding; sums exactly."""
    weights = [1.0 / (rank + 1) ** 0.8 for rank in range(tenant_count)]
    total = sum(weights)
    raw = [vehicle_count * w / total for w in weights]
    sizes = [int(x) for x in raw]
    by_remainder = sorted(range(tenant_count), key=lambda i: raw[i] - sizes[i], reverse=True)
    for i in by_remainder[: vehicle_count - sum(sizes)]:
        sizes[i] += 1
    return sizes


def generate_roster(
    vehicle_count: int,
    tenant_count: int = 20,
    seed: int = 42,
    driver_ratio: float = 0.9,
) -> Roster:
    if vehicle_count < tenant_count:
        raise ValueError("need at least one vehicle per tenant")
    if not 1 <= tenant_count <= len(_TENANT_WORDS):
        raise ValueError(f"tenant_count must be 1..{len(_TENANT_WORDS)}")

    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security-sensitive
    serial_by_oem: dict[str, int] = dict.fromkeys(OEM_BY_CODE, 0)
    model_weights = [m.fleet_share for m in VEHICLE_MODELS]

    tenants: list[Tenant] = []
    fleets: list[Fleet] = []
    workshops: list[Workshop] = []
    vehicles: list[Vehicle] = []
    drivers: list[Driver] = []
    driver_no = 0

    for t_index, size in enumerate(_tenant_sizes(vehicle_count, tenant_count)):
        first, second = _TENANT_WORDS[t_index]
        tenant = Tenant(_uuid(rng), f"{first}-{second}".lower(), f"{first} {second}")
        tenants.append(tenant)

        cities = rng.sample(CITIES, k=rng.randint(2, 4))
        city_fleets: list[tuple[Fleet, list[Workshop]]] = []
        for city, lat, lon in cities:
            fleet = Fleet(_uuid(rng), tenant.tenant_id, f"{city} Fleet", city)
            fleets.append(fleet)
            shops = [
                Workshop(
                    _uuid(rng),
                    tenant.tenant_id,
                    f"{city} Service Centre {n + 1}",
                    city,
                    round(lat + rng.uniform(-0.08, 0.08), 6),
                    round(lon + rng.uniform(-0.08, 0.08), 6),
                    rng.choice((8, 12, 16, 24)),
                )
                for n in range(rng.randint(1, 2))
            ]
            workshops.extend(shops)
            city_fleets.append((fleet, shops))

        for _ in range(size):
            fleet, shops = rng.choice(city_fleets)
            model = rng.choices(VEHICLE_MODELS, weights=model_weights)[0]
            oem = OEM_BY_CODE[model.oem_code]
            serial_by_oem[oem.code] += 1
            commissioned = date(2019, 1, 1) + timedelta(days=rng.randrange(0, 7 * 365))
            model_year = min(commissioned.year + rng.choice((0, 0, 1)), 2026)
            vin = build_vin(oem.vin_wmi, model.model_code, model_year, "C", serial_by_oem[oem.code])
            if not oem.requires_vin_check_digit and rng.random() < 0.5:
                # Markets without a mandatory check digit: position 9 is arbitrary.
                vin = vin[:8] + rng.choice(_CHECK_DIGIT_OFF_CHARS) + vin[9:]
            home = rng.choice(shops)
            vehicle = Vehicle(
                vehicle_id=_uuid(rng),
                tenant_id=tenant.tenant_id,
                fleet_id=fleet.fleet_id,
                vin=vin,
                oem_code=oem.code,
                model_code=model.model_code,
                powertrain=model.powertrain,
                model_year=model_year,
                firmware_version=rng.choice(model.firmware_versions),
                commissioned_on=commissioned,
                home_workshop_id=home.workshop_id,
                home_latitude=round(home.latitude + rng.uniform(-0.05, 0.05), 6),
                home_longitude=round(home.longitude + rng.uniform(-0.05, 0.05), 6),
            )
            vehicles.append(vehicle)
            if rng.random() < driver_ratio:
                driver_no += 1
                drivers.append(
                    Driver(_uuid(rng), tenant.tenant_id, f"DRV-{driver_no:06d}", vehicle.vehicle_id)
                )

    return Roster(tuple(tenants), tuple(fleets), tuple(workshops), tuple(vehicles), tuple(drivers))

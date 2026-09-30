"""VIN -> (vehicle_id, tenant_id, oem) lookup used for enrichment.

Loaded from PostgreSQL (the system of record) at startup and refreshed
periodically, so a newly onboarded vehicle becomes valid without a restart.
Lookups are O(1) dict reads; ~100K entries use ~30 MB.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from prognos_common.roster import Roster

log = logging.getLogger(__name__)

_QUERY = """
SELECT v.vin, v.vehicle_id::text, v.tenant_id::text, m.oem_code
FROM vehicles v JOIN vehicle_models m USING (model_code)
WHERE v.status <> 'retired'
"""


@dataclass(frozen=True, slots=True)
class VehicleRef:
    vehicle_id: str
    tenant_id: str
    oem: str


class VehicleRegistry:
    def __init__(self, entries: dict[str, VehicleRef], dsn: str | None = None) -> None:
        self.entries = entries
        self.dsn = dsn
        self.loaded_at = time.monotonic()

    def get(self, vin: str) -> VehicleRef | None:
        return self.entries.get(vin)

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def from_roster(cls, roster: Roster) -> VehicleRegistry:
        return cls(
            {
                v.vin: VehicleRef(str(v.vehicle_id), str(v.tenant_id), v.oem_code)
                for v in roster.vehicles
            }
        )

    @classmethod
    def from_postgres(cls, dsn: str) -> VehicleRegistry:
        registry = cls({}, dsn)
        registry.refresh()
        return registry

    def refresh(self) -> None:
        if self.dsn is None:
            return
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(_QUERY).fetchall()
        self.entries = {vin.strip(): VehicleRef(vid, tid, oem) for vin, vid, tid, oem in rows}
        self.loaded_at = time.monotonic()
        log.info("vehicle registry loaded: %d vehicles", len(self.entries))

    def refresh_if_older_than(self, seconds: float) -> None:
        if self.dsn and time.monotonic() - self.loaded_at >= seconds:
            try:
                self.refresh()
            except Exception:  # keep serving the last good copy
                log.exception("registry refresh failed; keeping %d cached vehicles", len(self))
                self.loaded_at = time.monotonic()

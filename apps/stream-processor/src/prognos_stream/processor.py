"""Pure normalisation pipeline for one raw message (no Kafka here, fully unit-testable).

raw bytes + headers
  -> parse JSON                         malformed_json
  -> resolve adapter by (oem, schema)   unknown_oem / unknown_schema_version
  -> adapt shape + units                invalid_type / invalid_timestamp / unsupported_unit
  -> validate                           missing_required_field / invalid_vin /
                                        out_of_range / invalid_event_type /
                                        future_timestamp / stale_event
  -> enrich (VIN registry)              unknown_vehicle / oem_mismatch
  -> deduplicate (sequence window)      -> dropped as duplicate
  -> canonical JSON keyed by vehicle_id
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

import orjson

from prognos_common.catalog import OEM_BY_CODE
from prognos_common.dtc import is_valid_dtc
from prognos_common.vin import VIN_PATTERN, is_valid_vin
from prognos_stream.adapters import Rejected, resolve
from prognos_stream.canonical import EVENT_TYPES, RANGES, REQUIRED, SCHEMA_VERSION, event_id
from prognos_stream.dedup import SequenceWindow, Verdict
from prognos_stream.registry import VehicleRegistry

CANONICAL, DLQ, DUPLICATE = "canonical", "dlq", "duplicate"
_RANGE_ITEMS = tuple(RANGES.items())
_second_prefix: dict[int, str] = {}


def _iso(ts: float) -> str:
    """ISO-8601 UTC, millisecond precision; the per-second prefix is cached."""
    sec = int(ts)
    prefix = _second_prefix.get(sec)
    if prefix is None:
        if len(_second_prefix) > 16_384:
            _second_prefix.clear()
        prefix = _second_prefix[sec] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec))
    return f"{prefix}.{int((ts - sec) * 1000):03d}Z"


@dataclass(slots=True)
class Outcome:
    kind: str  # canonical | dlq | duplicate
    key: bytes = b""
    value: bytes = b""
    reason: str = ""
    detail: str = ""
    late: bool = False


class Normalizer:
    def __init__(
        self,
        registry: VehicleRegistry,
        *,
        window: int = 1024,
        max_future_skew_s: float = 300.0,
        max_age_s: float = 7 * 86_400.0,
    ) -> None:
        self.registry = registry
        self.window_width = window
        self.windows: dict[int, SequenceWindow] = {}  # per source partition
        self.max_future_skew_s = max_future_skew_s
        self.max_age_s = max_age_s
        self.counts: Counter[str] = Counter()
        self._vin_cache: dict[tuple[str, str], bool] = {}

    def forget_partition(self, partition: int) -> None:
        """Called on partition revocation: the new owner rebuilds its own windows."""
        self.windows.pop(partition, None)

    def process(
        self, value: bytes | None, headers: dict[str, bytes], partition: int, now: float
    ) -> Outcome:
        try:
            outcome = self._process(value, headers, partition, now)
        except Rejected as rejected:
            outcome = Outcome(DLQ, reason=rejected.reason, detail=rejected.detail)
        except Exception as exc:  # a bug must not stall the partition: DLQ it and move on
            outcome = Outcome(DLQ, reason="processing_error", detail=type(exc).__name__)
        self.counts[outcome.kind] += 1
        if outcome.kind == DLQ:
            self.counts[f"dlq_{outcome.reason}"] += 1
        elif outcome.late:
            self.counts["late"] += 1
        return outcome

    # ------------------------------------------------------------------ pipeline
    def _process(
        self, value: bytes | None, headers: dict[str, bytes], partition: int, now: float
    ) -> Outcome:
        if not value:
            raise Rejected("malformed_json", "empty payload")
        try:
            payload = orjson.loads(value)
        except orjson.JSONDecodeError:
            raise Rejected("malformed_json") from None
        if not isinstance(payload, dict):
            raise Rejected("malformed_json", "not an object")

        oem_header = headers.get("oem")
        schema_header = headers.get("schema")
        oem = oem_header.decode() if oem_header else None
        adapter = resolve(oem, schema_header.decode() if schema_header else None)
        event = adapter(payload)
        assert oem is not None  # resolve() rejects a missing OEM

        self._validate(event, oem, now)
        ref = self.registry.get(event["vin"])
        if ref is None:
            raise Rejected("unknown_vehicle", event["vin"])
        if ref.oem != oem:
            raise Rejected("oem_mismatch", f"{event['vin']} registered to {ref.oem}")

        window = self.windows.get(partition)
        if window is None:
            window = self.windows[partition] = SequenceWindow(self.window_width)
        verdict = window.check(ref.vehicle_id, event["seq"])
        if verdict is Verdict.DUPLICATE:
            return Outcome(DUPLICATE)
        late = verdict in (Verdict.NEW_LATE, Verdict.TOO_OLD)

        dtcs = sorted({code for code in event.pop("dtc_raw") if is_valid_dtc(code)})
        canonical = self._canonical(event, ref.vehicle_id, ref.tenant_id, oem, dtcs, now, late)
        return Outcome(CANONICAL, ref.vehicle_id.encode(), orjson.dumps(canonical), late=late)

    def _validate(self, e: dict[str, Any], oem: str, now: float) -> None:
        for field in REQUIRED:
            if e.get(field) is None:
                raise Rejected("missing_required_field", field)
        vin = e["vin"]
        # VIN validity never changes, so cache it (bounded): the check digit alone
        # was ~3 us/event, repeated for the same 100K VINs every second.
        cache_key = (vin, oem)
        valid = self._vin_cache.get(cache_key) if type(vin) is str else False
        if valid is None:
            valid = bool(VIN_PATTERN.fullmatch(vin)) and is_valid_vin(
                vin, require_check_digit=OEM_BY_CODE[oem].requires_vin_check_digit
            )
            if len(self._vin_cache) >= 500_000:
                self._vin_cache.clear()
            self._vin_cache[cache_key] = valid
        if not valid:
            raise Rejected("invalid_vin", str(vin)[:32])
        if e["seq"] < 0:
            raise Rejected("out_of_range", "seq")
        if e["event_type"] not in EVENT_TYPES:
            raise Rejected("invalid_event_type", str(e["event_type"])[:32])
        ts = e["event_ts"]
        if ts > now + self.max_future_skew_s:
            raise Rejected("future_timestamp", f"{ts - now:.0f}s ahead")
        if ts < now - self.max_age_s:
            raise Rejected("stale_event", f"{now - ts:.0f}s old")
        for field, (low, high) in _RANGE_ITEMS:
            v = e[field]
            if v is not None and not low <= v <= high:
                raise Rejected("out_of_range", field)

    @staticmethod
    def _canonical(
        e: dict[str, Any],
        vehicle_id: str,
        tenant_id: str,
        oem: str,
        dtcs: list[str],
        now: float,
        late: bool,
    ) -> dict[str, Any]:
        """Adapters already emit canonical names, units and rounding; add identity fields."""
        e["schema_version"] = SCHEMA_VERSION
        e["event_id"] = event_id(vehicle_id, e["seq"])
        e["tenant_id"] = tenant_id
        e["vehicle_id"] = vehicle_id
        e["oem"] = oem
        e["event_ts"] = _iso(e["event_ts"])
        e["ingest_ts"] = _iso(now)
        e["dtc_codes"] = dtcs
        e["late"] = late
        return e

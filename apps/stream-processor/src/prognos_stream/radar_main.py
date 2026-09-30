"""prognos-radar: telemetry.canonical -> fleet.signals (emerging cross-vehicle DTC patterns).

    prognos-radar                  # config from environment

The radar needs the whole fleet in one place, so it runs as a single consumer
(group `radar`) over every partition. It only parses events that carry a DTC
(a byte check skips the rest), which keeps one process well ahead of the fleet.

Window state is in memory: after a restart the window in progress starts again
from the committed offset, so a signal can be delayed by up to one window but
never duplicated in a way that matters (signals are keyed by cohort, DTC and window).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import orjson
from prometheus_client import Counter, Gauge, start_http_server

from prognos_common.catalog import VEHICLE_MODELS
from prognos_common.logs import configure
from prognos_common.roster import Roster
from prognos_stream.kafka_loop import BatchService, LoopConfig, Record
from prognos_stream.main import wait_for_registry
from prognos_stream.planner_main import dsn_from_env
from prognos_stream.radar import Cohort, Radar, RadarConfig

log = logging.getLogger("prognos_stream")

SIGNALS = Counter("prognos_radar_signals", "Emerging-fault signals emitted", ["level"])
WINDOW_DTC_EVENTS = Gauge("prognos_radar_dtc_events", "DTC-bearing events seen (cumulative)")
EMPTY_DTCS = b'"dtc_codes":[]'
POWERTRAIN = {m.model_code: m.powertrain.value for m in VEHICLE_MODELS}

_COHORTS = """
SELECT v.vehicle_id::text, m.oem_code, m.model_code, v.firmware_version, m.powertrain
FROM vehicles v JOIN vehicle_models m USING (model_code)
WHERE v.status <> 'retired'
"""


def cohorts_from_postgres(dsn: str) -> dict[str, Cohort]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        rows = conn.execute(_COHORTS).fetchall()
    return {vid: Cohort(oem, model.strip(), fw, pt) for vid, oem, model, fw, pt in rows}


def cohorts_from_roster(roster: Roster) -> dict[str, Cohort]:
    return {
        str(v.vehicle_id): Cohort(
            v.oem_code, v.model_code, v.firmware_version, POWERTRAIN[v.model_code]
        )
        for v in roster.vehicles
    }


def event_ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def signal_record(topic: str, signal: dict[str, Any]) -> Record:
    fw = signal["firmware_version"] or "*"
    key = f"{signal['model_code']}/{fw}/{signal['dtc']}".encode()
    return (topic, key, orjson.dumps(signal), None)


@dataclass
class RadarServiceConfig(LoopConfig):
    group_id: str = "radar"
    input_topic: str = "telemetry.canonical"
    output_topic: str = "fleet.signals"
    registry_refresh_s: float = 300.0

    @classmethod
    def from_env(cls) -> RadarServiceConfig:
        env = os.environ
        return cls(
            bootstrap_servers=env.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            group_id=env.get("RADAR_GROUP_ID", cls.group_id),
            batch_size=int(env.get("RADAR_BATCH_SIZE", cls.batch_size)),
            exit_when_idle_s=float(env.get("EXIT_WHEN_IDLE_SECONDS", cls.exit_when_idle_s)),
            registry_refresh_s=float(env.get("REGISTRY_REFRESH_SECONDS", cls.registry_refresh_s)),
        )


class RadarService(BatchService):
    service_name = "radar"

    def __init__(self, cfg: RadarServiceConfig, radar: Radar, dsn: str | None = None) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.radar = radar
        self.dsn = dsn
        self.signals: list[dict[str, Any]] = []
        self._refreshed = 0.0

    def handle(self, msg: Any, now: float) -> Iterable[Record]:
        value: bytes = msg.value() or b""
        if EMPTY_DTCS in value:
            # Cheap path: no DTC. Still advance event time using the Kafka timestamp
            # (producer time ~ event time for on-time events) so windows close.
            _kind, ts_ms = msg.timestamp()
            found = self.radar.advance(ts_ms / 1000.0) if ts_ms > 0 else []
        else:
            try:
                e = orjson.loads(value)
                found = self.radar.add(e["vehicle_id"], event_ts(e["event_ts"]), e["dtc_codes"])
            except (orjson.JSONDecodeError, KeyError, ValueError, TypeError):
                return ()
        return [self._emit(s) for s in found]

    def _emit(self, signal: dict[str, Any]) -> Record:
        SIGNALS.labels(signal["level"]).inc()
        self.signals.append(signal)
        log.warning(
            "emerging fault: %s on %s %s (%d/%d vehicles, x%.1f baseline, p_adj=%.2g)",
            signal["dtc"], signal["model_code"], signal["firmware_version"] or "(all firmware)",
            signal["affected_vehicles"], signal["cohort_vehicles"], signal["rate_ratio"],
            signal["p_adjusted"],
        )  # fmt: skip
        return signal_record(self.cfg.output_topic, signal)

    def after_batch(self, consumed: int, elapsed: float) -> None:
        WINDOW_DTC_EVENTS.set(self.radar.counts["dtc_events"])
        self._maybe_refresh()

    def on_idle(self) -> None:
        self._maybe_refresh()

    def _maybe_refresh(self) -> None:
        if not self.dsn or time.monotonic() - self._refreshed < self.cfg.registry_refresh_s:
            return
        self._refreshed = time.monotonic()
        try:
            self.radar.set_cohorts(cohorts_from_postgres(self.dsn))
        except Exception:  # keep the last good cohort map
            log.exception("cohort refresh failed")


def main(argv: list[str] | None = None) -> int:
    configure("radar")
    parser = argparse.ArgumentParser(description="Prognos emerging-fault radar")
    parser.add_argument("--summary", type=Path, help="write signals + counters here on exit")
    args = parser.parse_args(argv)
    env = os.environ
    dsn = dsn_from_env()
    cohorts = wait_for_registry(
        lambda: cohorts_from_postgres(dsn),
        timeout_s=float(env.get("REGISTRY_WAIT_SECONDS", "300")),
    )
    radar = Radar(
        cohorts,
        RadarConfig(
            window_s=float(env.get("RADAR_WINDOW_SECONDS", "3600")),
            allowed_lateness_s=float(env.get("RADAR_ALLOWED_LATENESS_SECONDS", "120")),
            min_vehicles=int(env.get("RADAR_MIN_VEHICLES", "5")),
            min_ratio=float(env.get("RADAR_MIN_RATIO", "3")),
            alpha=float(env.get("RADAR_ALPHA", "0.001")),
        ),
    )
    if port := int(env.get("METRICS_PORT", "9105")):
        start_http_server(port)
    service = RadarService(RadarServiceConfig.from_env(), radar, dsn)
    service._refreshed = time.monotonic()
    service.install_signal_handlers()
    stats = service.run()
    summary = {"consumed": int(stats["consumed"]), "counts": radar.counts,
               "signals": service.signals}  # fmt: skip
    log.info(json.dumps({"event": "radar_summary", "counts": radar.counts}))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

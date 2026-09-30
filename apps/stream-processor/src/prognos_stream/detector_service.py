"""Detector service: telemetry.canonical -> alerts + vehicle.state (compacted).

Consumer group `detector`. Canonical events are keyed by vehicle_id, so all of a
vehicle's events arrive on one partition and its streaming state lives in exactly
one process. On revocation that state is dropped and rebuilt by the new owner
(trend statistics re-warm within minutes; see ADR-005 consequences).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import orjson
from prometheus_client import Counter, Gauge, Histogram

from prognos_stream.detector import Detector
from prognos_stream.kafka_loop import BatchService, LoopConfig, Record

ALERTS = Counter("prognos_detector_alerts", "Alert transitions", ["rule", "severity", "status"])
ALERT_LATENCY = Histogram(
    "prognos_detector_alert_latency_seconds",
    "Opened alerts: detection time minus the triggering event's device timestamp",
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 30, 60),
)
VEHICLES = Gauge("prognos_detector_vehicles_tracked", "Vehicles with detector state")
MALFORMED = Counter("prognos_detector_malformed", "Canonical records that failed to parse")


@dataclass
class DetectorConfig(LoopConfig):
    group_id: str = "detector"
    input_topic: str = "telemetry.canonical"
    alerts_topic: str = "alerts"
    state_topic: str = "vehicle.state"
    state_interval_s: float = 15.0

    @classmethod
    def from_env(cls) -> DetectorConfig:
        env = os.environ
        return cls(
            bootstrap_servers=env.get("KAFKA_BOOTSTRAP_SERVERS", cls.bootstrap_servers),
            group_id=env.get("DETECTOR_GROUP_ID", cls.group_id),
            batch_size=int(env.get("DETECTOR_BATCH_SIZE", cls.batch_size)),
            state_interval_s=float(env.get("STATE_INTERVAL_SECONDS", cls.state_interval_s)),
            exit_when_idle_s=float(env.get("EXIT_WHEN_IDLE_SECONDS", cls.exit_when_idle_s)),
        )


class DetectorService(BatchService):
    service_name = "detector"

    def __init__(self, cfg: DetectorConfig, detector: Detector | None = None) -> None:
        super().__init__(cfg)
        self.cfg = cfg
        self.detector = detector or Detector(state_interval_s=cfg.state_interval_s)

    def on_partitions_revoked(self, partitions: list[int]) -> None:
        for partition in partitions:
            self.detector.forget_partition(partition)

    def handle(self, msg: Any, now: float) -> Iterable[Record]:
        try:
            event = orjson.loads(msg.value())
        except orjson.JSONDecodeError:
            MALFORMED.inc()
            return ()
        records: list[Record] = []
        for out in self.detector.process(event, msg.partition(), now):
            topic = self.cfg.alerts_topic if out.topic == "alerts" else self.cfg.state_topic
            records.append((topic, out.key, out.value, None))
            if out.topic == "alerts":
                alert = orjson.loads(out.value)
                ALERTS.labels(alert["rule_code"], alert["severity"], alert["status"]).inc()
        return records

    def after_batch(self, consumed: int, elapsed: float) -> None:
        latencies = self.detector.alert_latencies
        for value in latencies:
            ALERT_LATENCY.observe(value)
        latencies.clear()
        VEHICLES.set(self.detector.vehicles_tracked())

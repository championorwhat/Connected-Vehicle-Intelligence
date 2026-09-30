"""Generate the Grafana dashboards (checked in; a test keeps them in sync with the metrics).

    uv run python scripts/gen_dashboards.py

Dashboards are code: every panel's PromQL is reviewed like any other change, and
tests/unit/test_observability.py fails if a panel or alert refers to a metric that no
service exports. Colours follow the entity (a service keeps its hue on every panel);
status colours are reserved for SLO thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "infra/monitoring/grafana/dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}

# Categorical order (validated for the dark surface, CVD-safe): fixed per entity.
SERIES = {
    "normalizer": "#3987e5",
    "detector": "#d95926",
    "sink": "#199e70",
    "radar": "#c98500",
    "clickhouse": "#d55181",
}
NEUTRAL = "#8e8e8e"  # no data
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}


def quantile(q: float, metric: str, by: str = "le") -> str:
    """PromQL for the q-th percentile of a histogram over 5 minutes."""
    return f"histogram_quantile({q}, sum by ({by}) (rate({metric}_bucket[5m])))"


class Board:
    def __init__(self, uid: str, title: str, description: str) -> None:
        self.uid, self.title, self.description = uid, title, description
        self.panels: list[dict[str, Any]] = []
        self.y = 0
        self.x = 0
        self.row_h = 0

    def row(self, title: str) -> None:
        self._newline()
        self.panels.append(
            {
                "type": "row",
                "title": title,
                "collapsed": False,
                "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1},
                "panels": [],
            }
        )
        self.y += 1

    def _place(self, w: int, h: int) -> dict[str, int]:
        if self.x + w > 24:
            self._newline()
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def _newline(self) -> None:
        if self.x:
            self.y += self.row_h
        self.x, self.row_h = 0, 0

    def stat(
        self,
        title: str,
        expr: str,
        unit: str,
        steps: list[tuple[float | None, str]],
        description: str,
        w: int = 4,
        decimals: int = 2,
        no_value: str = "No data",
    ) -> None:
        # No data is shown in neutral grey, never in a status colour: it is not "good".
        null = {
            "type": "special",
            "options": {"match": "null", "result": {"text": no_value, "color": NEUTRAL}},
        }
        self.panels.append(
            {
                "type": "stat",
                "title": title,
                "description": description,
                "datasource": DS,
                "gridPos": self._place(w, 4),
                "targets": [{"expr": expr, "refId": "A", "instant": True, "datasource": DS}],
                "options": {
                    "colorMode": "background",
                    "graphMode": "none",
                    "textMode": "value",
                    "reduceOptions": {"calcs": ["lastNotNull"]},
                },
                "fieldConfig": {
                    "defaults": {
                        "unit": unit,
                        "decimals": decimals,
                        "noValue": no_value,
                        "mappings": [null],
                        "thresholds": {
                            "mode": "absolute",
                            "steps": [{"value": v, "color": STATUS[c]} for v, c in steps],
                        },
                    },
                    "overrides": [],
                },
            }
        )

    def timeseries(
        self,
        title: str,
        targets: list[tuple[str, str]],
        unit: str,
        description: str,
        w: int = 12,
        h: int = 8,
        threshold: float | None = None,
        stack: bool = False,
    ) -> None:
        overrides = [
            {
                "matcher": {"id": "byName", "options": name},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
            }
            for name, color in SERIES.items()
        ]
        defaults: dict[str, Any] = {
            "unit": unit,
            "custom": {
                "lineWidth": 2,
                "fillOpacity": 10 if stack else 0,
                "showPoints": "never",
                "stacking": {"mode": "normal" if stack else "none"},
                "thresholdsStyle": {"mode": "line+area" if threshold is not None else "off"},
            },
            "color": {"mode": "palette-classic"},
        }
        if threshold is not None:  # the SLO target as a dashed line, breaches shaded
            defaults["thresholds"] = {
                "mode": "absolute",
                "steps": [
                    {"value": None, "color": "transparent"},
                    {"value": threshold, "color": STATUS["critical"]},
                ],
            }
        self.panels.append(
            {
                "type": "timeseries",
                "title": title,
                "description": description,
                "datasource": DS,
                "gridPos": self._place(w, h),
                "targets": [
                    {"expr": e, "legendFormat": leg, "refId": chr(65 + i), "datasource": DS}
                    for i, (e, leg) in enumerate(targets)
                ],
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"},
                },
                "fieldConfig": {"defaults": defaults, "overrides": overrides},
            }
        )

    def json(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": ["prognos"],
            "timezone": "utc",
            "schemaVersion": 41,
            "version": 1,
            "refresh": "10s",
            "time": {"from": "now-1h", "to": "now"},
            "panels": self.panels,
            "templating": {"list": []},
            "annotations": {"list": []},
        }


def overview() -> Board:
    b = Board(
        "prognos-overview",
        "Prognos · service health",
        "SLOs first, then the pipeline stages in data-flow order. Definitions: "
        "docs/observability/slo.md",
    )
    b.row("Service level objectives (targets in docs/observability/slo.md)")
    b.stat(
        "API availability (1 h)",
        "1 - prognos:api_errors:ratio_rate1h",
        "percentunit",
        [(None, "critical"), (0.995, "good")],
        "Share of requests without a 5xx. SLO 99.5 %.",
        no_value="no API traffic",
    )
    b.stat(
        "Alerts raised within 5 s (1 h)",
        "1 - prognos:alert_latency_slow:ratio_rate1h",
        "percentunit",
        [(None, "critical"), (0.95, "good")],
        "Device timestamp to alert raised. SLO 95 % within 5 s (brief).",
    )
    b.stat(
        "Worst stream delay",
        "max(prognos:stream_seconds_behind)",
        "s",
        [(None, "good"), (30, "warning"), (60, "critical")],
        "Seconds of traffic the slowest consumer group is behind. Alert above 60 s.",
    )
    b.stat(
        "Quarantined telemetry (10 m)",
        "prognos:dlq:ratio_rate10m",
        "percentunit",
        [(None, "good"), (0.03, "warning"), (0.05, "critical")],
        "Share of raw messages sent to the DLQ. Injected faults keep this near 2 %.",
    )
    b.stat(
        "Model scores age",
        "time() - prognos_scorer_last_success_timestamp_seconds",
        "s",
        [(None, "good"), (600, "warning"), (900, "critical")],
        "Time since the last scoring cycle (shadow mode). Stale above 15 min.",
        decimals=0,
        no_value="scorer not running",
    )
    b.stat(
        "Alerts firing",
        'count(ALERTS{alertstate="firing"}) or vector(0)',
        "none",
        [(None, "good"), (1, "critical")],
        "Prometheus alerts currently firing.",
        decimals=0,
    )

    b.row("Pipeline: throughput and freshness")
    b.timeseries(
        "Messages processed per second",
        [("sum by (service) (rate(prognos_stream_consumed_total[1m]))", "{{service}}")],
        "ops",
        "Per consumer group; scale out with replicas, up to the partition count.",
    )
    b.timeseries(
        "Seconds behind the stream",
        [("prognos:stream_seconds_behind", "{{service}}")],
        "s",
        "Backlog divided by throughput. Page above 60 s for 5 min.",
        threshold=60,
    )
    b.timeseries(
        "Critical alert latency",
        [
            (quantile(0.5, "prognos_detector_alert_latency_seconds"), "p50"),
            (quantile(0.95, "prognos_detector_alert_latency_seconds"), "p95"),
            (quantile(0.99, "prognos_detector_alert_latency_seconds"), "p99"),
        ],
        "s",
        "Device timestamp to alert raised. The line marks the 5 s target.",
        threshold=5,
    )
    b.timeseries(
        "Alerts opened per minute by severity",
        [
            (
                '60 * sum by (severity) (rate(prognos_detector_alerts_total{status="open"}[5m]))',
                "{{severity}}",
            )
        ],
        "none",
        "Opened alert transitions from the detector.",
    )

    b.row("Data quality")
    b.timeseries(
        "Quarantined messages per second by reason",
        [("sum by (reason) (rate(prognos_normalizer_dlq_total[5m]))", "{{reason}}")],
        "ops",
        "Why messages go to the DLQ (telemetry.dlq keeps the original bytes).",
        stack=True,
    )
    b.timeseries(
        "Normaliser outcomes per second",
        [("sum by (kind) (rate(prognos_normalizer_outcomes_total[5m]))", "{{kind}}")],
        "ops",
        "canonical = forwarded; duplicate = dropped by the sequence window.",
    )

    b.row("API")
    b.timeseries(
        "Requests per second by route",
        [("sum by (route) (rate(prognos_api_requests_total[5m]))", "{{route}}")],
        "reqps",
        "All tenants. Rate-limited at 600/min per user.",
    )
    b.timeseries(
        "p95 latency by route",
        [(quantile(0.95, "prognos_api_request_seconds", by="le, route"), "{{route}}")],
        "s",
        "Latency SLO: p95 below 0.5 s.",
        threshold=0.5,
    )

    b.row("Decisions: model, planner, radar")
    b.timeseries(
        "Vehicles scored vs held back by the data gate",
        [
            ("prognos_scorer_vehicles_scored", "scored"),
            ("prognos_scorer_vehicles_insufficient_data", "not enough data"),
        ],
        "none",
        "Scored only with 45 of the last 60 minutes of data (ADR-008).",
        w=8,
    )
    b.timeseries(
        "Work orders proposed per hour",
        [
            ("sum(increase(prognos_planner_work_orders_proposed_total[1h]))", "proposed"),
            ("sum(increase(prognos_planner_late_proposals_total[1h]))", "after predicted failure"),
        ],
        "none",
        "Planner output; late = booked after the predicted failure.",
        w=8,
    )
    b.timeseries(
        "Emerging-fault signals per hour",
        [("sum by (level) (increase(prognos_radar_signals_total[1h]))", "{{level}}")],
        "none",
        "Radar signals by cohort level (firmware / model).",
        w=8,
    )
    return b


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for board in (overview(),):
        (OUT / f"{board.uid}.json").write_text(json.dumps(board.json(), indent=2) + "\n")
        print(f"wrote {board.uid}.json ({len(board.panels)} panels)")


if __name__ == "__main__":
    main()

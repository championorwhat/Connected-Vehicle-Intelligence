# Service level objectives

What "working" means for Prognos, measured from metrics the services export
(recording rules in [prognos.rules.yml](../../infra/monitoring/prometheus/rules/prognos.rules.yml)).
Each rule and alert is unit-tested with `promtool test rules`
([tests](../../infra/monitoring/prometheus/tests/prognos.test.yml)).

| SLO | SLI (how it is measured) | Target | Error budget | Alert |
|---|---|---|---|---|
| **Critical alerts are fast** (the brief's requirement) | share of opened alerts raised within 5 s of the device timestamp (`prognos_detector_alert_latency_seconds`) | 95 % | 5 % of alerts may be slower | `AlertLatencySLOBurn`: page at more than 10 % slow over 1 h and 5 min |
| **The API is available** | share of API requests without a 5xx (`prognos_api_requests_total`) | 99.5 % over 30 days | 0.5 % (about 3.6 h a month) | `ApiErrorBudgetFastBurn`: page at a 14.4× burn over 1 h and 5 min. `ApiErrorBudgetSlowBurn`: ticket at 6× over 6 h and 30 min |
| **The API is fast** | p95 of `prognos_api_request_seconds` per route | below 0.5 s | – | Dashboard only (NOT YET MEASURED under load, see M14) |
| **Data is fresh** | seconds of traffic each consumer group is behind (backlog ÷ throughput) | below 60 s | – | `PipelineFallingBehind` after 5 min; `PipelineStalled` when nothing is consumed |
| **Data is clean** | share of raw telemetry sent to the DLQ | below 5 % | – | `DeadLetterRateHigh` after 10 min |

## Why burn-rate alerts

A threshold alert such as "error rate > 1 %" either fires on every short blip or
misses a slow leak. Burn-rate alerts ask a different question: *at this rate, when does
the monthly error budget run out?*

- **Fast burn.** A 14.4× burn over 1 h uses 2 % of the month's budget in an hour, so it
  pages. The 5-minute window must also be burning, so the alert clears as soon as the
  problem stops.
- **Slow burn.** A 6× burn over 6 h would exhaust the budget in about 5 days, so it
  raises a ticket.

This is the multi-window, multi-burn-rate method from Google's *SRE Workbook* ("Alerting
on SLOs").

## Scope and honesty

- These are **targets**. They have not been measured over 30 days; the stack has only
  run for demo sessions.
- The one SLI measured end to end is critical alert latency: p50 0.50 s, p95 1.1 s, p99
  3.6 s ([M5 evidence](../../evidence/benchmarks/m5-alert-latency-live.json)). That is
  inside the target.
- Kafka-level metrics (broker, ISR, disk) are not exported yet. Consumer lag is
  measured by the consumers themselves (`prognos_stream_consumer_lag`), which covers
  the "is data flowing" question. A broker exporter is planned for the cloud
  deployment (M16).
- Tracing: see [ADR-009](../architecture/adr/ADR-009.md). Correlation today uses
  request ids in JSON logs and the audit log, and event ids through the pipeline.

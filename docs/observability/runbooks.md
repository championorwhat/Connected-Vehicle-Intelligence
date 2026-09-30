# Runbooks

One section per alert in [prognos.rules.yml](../../infra/monitoring/prometheus/rules/prognos.rules.yml).
Each section says what the alert means, what to check first and how to fix it.
Dashboard: Grafana → Prognos → *service health* (`make up-obs`, http://localhost:3000).

## API errors

**Alerts:** `ApiErrorBudgetFastBurn` (page) and `ApiErrorBudgetSlowBurn` (ticket). More than
0.5 % of API requests fail with 5xx, fast enough to use up the monthly budget.

1. Open the *Requests per second by route* panel to see which route fails.
2. Search the API logs by request id. 5xx and slow requests are logged as JSON with
   `request_id`, `route`, `status` and `duration_ms`:
   `docker compose logs api | grep '"status": 5'`.
3. Check `/readyz`. If `postgres` is `down`, go to the database first: connections, disk,
   `docker compose ps postgres`.
4. A deploy just happened? Roll back the `api` image.

## Alert latency

**Alert:** `AlertLatencySLOBurn` (page). More than 10 % of critical alerts take longer than
5 s from the device timestamp to the alert (SLO: 95 % within 5 s).

1. *Seconds behind the stream*: if `normalizer` or `detector` is behind, this is a
   throughput problem. See [Falling behind](#falling-behind).
2. If nothing is behind, the delay is before Kafka: devices sending late (network
   latency, buffered uploads). Compare `event_ts` and `ingest_ts` in ClickHouse
   `events`. Delay on the device side is outside our control; record it and do not
   silence the alert.
3. A broker restart or a consumer rebalance also causes short spikes. The `for: 5m`
   clause absorbs brief ones.

## Falling behind

**Alert:** `PipelineFallingBehind` (page). A consumer group is more than 60 s behind the
stream.

1. *Messages processed per second*: has throughput dropped, or has input risen (a
   burst)?
2. Scale out: `NORMALIZER_REPLICAS=3 docker compose --profile pipeline up -d`. Up to 6
   replicas help, one per partition of `telemetry.raw` and `telemetry.canonical`.
3. Check CPU and memory limits (`docker stats`). A container at its memory limit is
   restarted repeatedly.
4. Kafka keeps telemetry for `KAFKA_TELEMETRY_RETENTION_MS` (1 day by default locally), so a
   group that catches up within that window loses nothing. A longer outage loses the oldest
   data: raise the retention before a planned long stop.

## Stalled

**Alert:** `PipelineStalled` (page). A group has a backlog but consumes nothing.

1. `docker compose ps` / `logs <service>`: is the process crash-looping, or waiting on
   a dependency? The sink waits up to `SINK_RETRY_BUDGET_SECONDS` for PostgreSQL and
   Redis, then restarts.
2. A poison message cannot block a partition: the normalizer sends it to the DLQ, and
   the sink isolates bad rows with savepoints. If the logs show the same offset failing
   repeatedly, capture it with `make dlq-peek` and file a bug.

## DLQ

**Alert:** `DeadLetterRateHigh` (ticket). More than 5 % of raw telemetry is quarantined
(normally about 2 %, from the simulator's injected faults).

1. *Quarantined messages per second by reason* shows which reason grew.
2. `unknown_vehicle`: new vehicles have not reached the registry yet. It refreshes every
   `REGISTRY_REFRESH_SECONDS`; check that onboarding wrote them to PostgreSQL.
3. `unknown_schema_version` or `unknown_oem`: an OEM changed its payload. Add an adapter
   version (the DLQ keeps the original bytes, so the messages can be replayed afterwards).

## Sink rejections

**Alert:** `SinkRejectingAlerts` (ticket). PostgreSQL rejected alert rows, which violated a
constraint.

1. The sink logs `alert rejected (<ErrorType>): <fingerprint>`. Usually the vehicle is
   missing from PostgreSQL (for example, deleted or erased).
2. The other rows in the batch were written; only the bad row is skipped (savepoints).
   `make reconcile` shows the difference.

## Stale loops

**Alerts:** `ScorerStale` and `PlannerStale` (ticket). A periodic job has not completed a
cycle in time.

1. Look at the logs of `scorer` or `planner`. Each cycle logs a summary; failures log
   an exception and retry at the next interval.
2. Scorer: ClickHouse unreachable, or the model files missing (`MODEL_DIR`). The planner
   and API keep working on the rules; the model is in shadow mode (ADR-008).
3. Planner: PostgreSQL unreachable. No new proposals are made; existing work orders are
   unaffected.

## Service down

**Alert:** `ServiceDown` (page). No healthy instance of a required service for 2 minutes.
This includes a service that has vanished completely. With Docker DNS discovery, a
stopped container disappears from Prometheus's target list, so only `absent(...)` can
see it (found in the M11 failure drill).

1. `docker compose ps <service>` and `docker compose logs --tail 100 <service>`. Look
   for a crash loop, an OOM kill (`docker inspect` → `OOMKilled`) or a failed
   dependency.
2. Restart it: `docker compose --profile pipeline up -d <service>`. Kafka consumers
   resume from their committed offsets, so nothing is lost; lag drains afterwards.
3. The scorer is optional (shadow mode). If it is not deployed on purpose, silence this
   alert for `job="scorer"`.

## Target down

**Alert:** `TargetDown` (page). One replica cannot be scraped for 2 minutes while the
service itself is still discovered (for example, one of three normalizers is hung).

1. `docker compose ps`: is the service running and healthy?
2. If it is running, check that its `METRICS_PORT` matches the scrape config
   (`infra/monitoring/prometheus/prometheus.yml`).

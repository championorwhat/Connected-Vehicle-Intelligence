# Failure drills

A monitoring setup is only trusted once it has seen a real failure. Each drill breaks one
thing on the running stack and records what the alerts and dashboards did.

## Drill 1: the detector disappears (M11, 2026-09-30)

Evidence: [m11-detector-outage-drill.json](../../evidence/chaos/m11-detector-outage-drill.json).
Measured on a 4-vCPU Linux container, not the target Mac.

**Setup.** 100,000 seeded vehicles, simulator at about 10,000 events/s, pipeline and
observability profiles running.

**Fault.** `docker compose stop detector`. This stops the only detector replica for 13 min
35 s.

![Grafana overview during the drill](../images/grafana-overview.png)

### What happened

| Time (UTC) | Event |
|---|---|
| 09:51:06 | Detector stopped |
| — | `TargetDown` **never fired**. This was a gap in the alert design. |
| ~10:02 | `ServiceDown` rules added and reloaded |
| 10:04:37 | `ServiceDown{job="detector"}` fires (page) |
| 10:04:41 | Detector restarted; it resumes from its committed Kafka offsets |
| 10:05:49 | Backlog peaks at 7,420,793 messages |
| 10:05:37–10:05:52 | `ServiceDown` resolves |
| 10:12:21 | Backlog drained (lag about 1,000, the normal in-flight level). The drain took 7 min 40 s, sustained at 28,000–29,500 msg/s against about 9,900 msg/s steady state |
| 10:10:52–10:17:07 | `AlertLatencySLOBurn` fires |

### Findings

1. **`up == 0` cannot see a stopped service.** Prometheus finds replicas through Docker
   DNS. A stopped container drops out of DNS, so its target disappears rather than going
   down, and no `up == 0` series exists to alert on. The fix is a `ServiceDown` alert per
   required job: `absent(up{job="X"} == 1)` for 2 minutes. It fired on the next
   evaluation and has a promtool test. `TargetDown` stays for the case of one of several
   replicas hanging.
2. **The SLO caught the user impact.** Alerts raised from backlog events were minutes late
   against device time, so the 5-second alert-latency SLO burned and paged after the
   restart. This is the intended behaviour: the SLO measures what fleet managers
   experience, not whether a process is up.
3. **`PipelineFallingBehind` stayed pending, correctly.** The detector was more than 60 s
   behind for about 4.6 minutes while draining, which is under the alert's `for: 5m`.
   The outage itself was already paged by `ServiceDown`.
4. **Seconds-behind is overstated right after a restart.** It peaked at 5,723 s because the
   5-minute consume rate still included the minutes of zero throughput. It converged
   within about a minute. The metric is an estimate for dashboards and alerts, not an
   exact freshness measure.
5. **Found while taking the screenshot:** the *API availability* tile showed "No data".
   With zero 5xx responses the numerator has no series, so the ratio had none either.
   The fix is `or vector(0)`, plus a promtool test. Tiles now show "No data" in grey,
   never in a status colour.

**Not verified in this drill:** end-to-end reconciliation after the outage. Offsets were
committed and Kafka keeps telemetry for 1 day by default (an outage of 13.5 minutes is far
inside that), so no loss is expected. The M4 and M6
reconciliations cover the no-loss property.

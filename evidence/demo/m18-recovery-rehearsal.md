# Demo rehearsal: recovery moment (M18, 2026-09-30)

4-vCPU x86_64 Linux container (not the target Mac); `make pipeline-demo`: 10,000
vehicles, 10,000 events/s. Detector backlog read from the Kafka broker
(`kafka-consumer-groups.sh --describe --group detector`), sampled by hand:

| Seconds | Action | Detector backlog (messages) |
|---|---|---|
| 3 | before | 1,684 |
| 3–4 | `docker compose stop detector` | |
| 6 | down | 26,668 |
| 9 | down | 52,211 |
| 12 | down | 80,894 |
| 12–35 | `docker compose start detector` (the command took 23 s to return) | |
| 38 | catching up | 343,990 |
| 42 | catching up | 300,994 |
| 45 | catching up | 218,837 |
| 48 | catching up | 129,232 |
| 52 | catching up | 40,222 |
| 55 | caught up | 53 |

The backlog grew at about 8.5K messages/s while the detector was down and drained at
about 20K messages/s after it restarted: 344K messages in about 17 s.

**Found:** the detector exports its own lag metric, so while it is stopped Prometheus has
no lag series for it and Grafana's "Seconds behind the stream" panel does **not** show the
backlog growing (a first attempt sampled Prometheus and saw exactly that). The demo
therefore shows the lag from the broker (`make lag`). Outage detection itself is covered
by the `ServiceDown` alert (absent series), found in the M11 drill.

# Demo script (5 minutes)

The timings below come from a real rehearsal of `make pipeline-demo`
([timeline](../../evidence/demo/m18-rehearsal-timeline.json),
[storyboard screenshots](../../evidence/demo/storyboard/)). The scripted failures run on
the simulator's clock, so the video is anchored to it: **start recording when the cue
sheet shows about 07:00.**

## Before recording (about 20 minutes ahead)

**On the Mac:**
- Docker Desktop with **Memory at 5 GB or more** (the demo stack used about 2.7 GB in the
  rehearsal).
- Close other heavy apps.

**Start from a clean state:**
```zsh
make clean              # deletes all local data (asks nothing: be sure)
make env                # then set DEMO_USER_PASSWORD in .env
make up-obs             # Kafka, PostgreSQL, ClickHouse, Redis, Prometheus, Grafana
make seed               # 10,000 vehicles
make pipeline-demo      # simulator (5 scripted failures + a firmware defect), pipeline, API, dashboard
make users              # demo logins, in the tenant where the failures happen
make demo-timeline      # leave this terminal visible: it prints each event as it happens
```

`make pipeline-demo` starts the simulator's clock. The first scripted event appears at
about **02:53** on the cue sheet.

**Open three browser tabs:**
1. http://localhost:8080. Sign in as `fleet_manager@demo.prognos.local` with
   `DEMO_USER_PASSWORD`.
2. http://localhost:3000 (Grafana, `GRAFANA_ADMIN_*` from `.env`): dashboard
   **Prognos · service health**, last 15 minutes, refresh 5 s.
3. The Solution Document on GitHub, §5.1 (architecture), or `docs/images/m14-burst.svg`.

**Also have ready:** a terminal in the repository, for the recovery moment.

## What happens when (from the rehearsal)

| Cue sheet | Vehicle | Event |
|---|---|---|
| 02:53 | PG3EC8S64NC000003 (Lyra e-Compact) | warning: TYRE_SLOW_LEAK; work order proposed at 03:02 |
| 05:33–05:48 | PG2PK5C82NC000001, PG2TR4D28MC000002 | warnings: misfire code, weak 12 V battery; work orders proposed at 06:03 |
| 06:34 | **PG1HL2B71KC000001 (Orion Hauler)** | warning: DTC_P0118 (coolant temperature); work order proposed at 07:03 |
| **08:18** | **PG1HL2B71KC000001** | **critical: COOLANT_OVERHEAT** (this is the live moment) |
| 08:35 | PG3ET9T5XRC000004 (Lyra e-Truck) | warning: HV_CELL_IMBALANCE; work order proposed at 09:03 |
| 10:00–10:21 | three vehicles | critical: 12 V battery, tyre pressure, misfire |
| **10:02** | **PG1HL2B71KC000001** | **VEHICLE_BREAKDOWN** (simulated) |
| 12:02–18:02 | the other four | VEHICLE_BREAKDOWN, one every two minutes |

**In the rehearsal, all five scripted failures were warned about and had a proposed work
order before the vehicle broke down**, between 3.5 and 13 minutes ahead on the demo's
compressed clock. Each alert was raised 0.15–1.18 s after the vehicle's reading.

Degradation is compressed in the demo: a fault that takes days in the simulator's normal
mode fails in about 10 minutes here, so it fits a video. Say so once, on camera.

## The script

Start recording when the cue sheet reads about **07:00**. The video clock (left) then
runs about 7 minutes behind the cue sheet.

| Video | Segment | Show | Say (about 75 words per 30 s) |
|---|---|---|---|
| **0:00–0:30** | Problem | The Fleet overview tab | "A fleet maintenance manager with 100,000 vehicles finds out about most faults when a vehicle breaks down at the roadside. Repair and maintenance already cost US trucking about 21.5 cents a mile last year, as reported from ATRI's cost study, and an unplanned repair costs more than a planned one. The manager needs to know which vehicle fails next, and why, early enough to book it into a workshop." |
| **0:30–1:00** | Solution | Stay on the overview: point at the tiles and the "Most at risk" list | "Prognos reads telemetry from three vehicle makes, raises a critical alert within seconds, ranks the vehicles likely to fail this week, and proposes workshop bookings within each workshop's capacity. Everything you see is a simulated fleet with injected faults, so we can check every prediction against what really happened. It never shows a money figure without a sourced cost." |
| **1:00–1:15** | Live demo: overview | Scroll the at-risk list; click **Rules** / **Model (shadow)** | "This is the largest tenant: 2,123 vehicles. The list is ranked by calibrated risk. The failure model runs in shadow mode: it is scored, but the rules stay in charge until the model is validated on real outcomes." |
| **1:15–1:40** | **Live critical alert** | Click **Alerts**. At cue ≈ 08:18, **COOLANT_OVERHEAT** for PG1HL2B71KC000001 appears in the live feed. Click **Acknowledge** | "Here it comes: an Orion Hauler is overheating. That alert reached this screen in under two seconds from the vehicle's reading. At 100,000 vehicles, 95 % of alerts arrive within 1.9 s. I acknowledge it, and that action is audited." |
| **1:40–2:05** | Why | Click the vehicle: **PG1HL2B71KC000001**. Show the alert history | "Prognos saw this coming. A coolant sensor code was raised about two minutes earlier, and the planner had already proposed a booking. The model panel says it needs 45 minutes of data for this vehicle, and it will not guess without them." |
| **2:05–2:35** | Act | Click **Work orders** → the **Cooling failure** row (100 %) → **Schedule** → accept the date → the **Scheduled** tab | "The planner proposed this booking at the nearest workshop with free capacity, before the predicted failure. Cost avoided says 'not sourced': we will not invent repair costs. I schedule it; the technician will see it and complete it with an outcome." |
| **2:35–3:00** | Fleet signals | Click **Fleet signals** | "One more thing no single-vehicle rule can see: fault code U0100 is spiking on the Lyra e-Van on firmware 2026.7, many times the rate of the same model on other firmware. An exact statistical test flags only that cohort. In our tests it found the injected defect in every window, with zero false signals." |
| **3:00–3:25** | Under the hood | Solution Document §5.1 container diagram, then the Grafana tab. At cue ≈ 10:02 the Hauler breaks down in the simulation | "Under the hood: Kafka; Python consumer groups; ClickHouse for history; PostgreSQL with row-level security for alerts and work orders; Redis for live state; FastAPI and React. As we speak, the Hauler we booked has just broken down in the simulation, about three and a half minutes after Prognos warned." |
| **3:25–4:15** | **Failure recovery**, with scale told while it drains | Terminal: `make lag LAG_GROUPS=detector`, then `docker compose stop detector`, then `make lag LAG_GROUPS=detector` twice (the backlog grows by about 8,500 a second). Then `docker compose start detector` (about 20 s to return), and `make lag LAG_GROUPS=detector` until it is near 0 (about 17 s) | "Now I stop the detector, the service that raises alerts. The broker keeps every event, so the backlog grows instead of data being lost: 26,000, then 52,000 messages. I start it again. While it catches up: at 100,000 vehicles reporting every 10 seconds, on four shared CPUs, every stage kept up, critical alerts had a p95 of 1.8 s against a 5-second target, and the API a p95 under 90 ms with 40 users. And it has caught up. We ran this drill with a database outage too: 3.77 million events, zero lost." |
| **4:15–4:45** | Impact | Solution Document §1 table | "Results, all measured and linked to evidence: 98.6 % of breakdowns warned, a median of 49.6 hours ahead in the simulator; the model beats the rules on held-out data, PR-AUC 0.74 against 0.51. What is not proven: real fleets, money, and 100,000 events per second end to end, which needs more than four CPUs." |
| **4:45–5:00** | Next steps and team | Same | "Next: a secure cloud deployment, a pilot on one real fleet with sourced costs, and then switching on the model. Prognos was built by Pratibimb Gupta, solo, with Claude Code as coding assistant. Thank you." |

## Checks while recording

- **The alert is late or missing at 1:15–1:40.** Keep talking over the overview. Every
  scripted failure repeats the pattern within the next two minutes: 12 V battery at cue
  ≈ 10:00, tyre at ≈ 10:17. Any of them works for the live moment.
- **The dashboard shows nothing.** You are probably signed in to the wrong tenant. Run
  `make users` again, then sign out and back in.
- **The recovery moment misbehaves.** The detector must be started again
  (`docker compose start detector`) before you stop recording. Do not rely on Grafana
  for this moment: while the detector is stopped, its own lag metric disappears, so the
  panel does not show the backlog. `make lag` asks the broker instead
  ([rehearsal](../../evidence/demo/m18-recovery-rehearsal.md)).
- **Stop at 5:00.** Longer videos are cut.
- **Recording:** 1080p with clear audio, captions if possible. Upload as unlisted.

## Numbers used in the narration

| Claim | Evidence |
|---|---|
| 95 % of alerts on the dashboard within 1.9 s (1.88 s) at 100K vehicles | [m17-dashboard-freshness-100k.json](../../evidence/load-tests/m17-dashboard-freshness-100k.json) |
| Critical alerts p95 1.78 s at 100K vehicles | [m14-100k.json](../../evidence/load-tests/m14-100k.json) |
| API p95 63–82 ms, 40 users | [m14-api-40-users-during-100k.json](../../evidence/load-tests/m14-api-40-users-during-100k.json) |
| Detector backlog +8.5K/s while stopped; 344K drained in about 17 s | [m18-recovery-rehearsal.md](../../evidence/demo/m18-recovery-rehearsal.md) |
| 3.77 M events, zero lost, with PostgreSQL down | [m13-postgres-outage-reconciliation.json](../../evidence/chaos/m13-postgres-outage-reconciliation.json) |
| 98.6 % of breakdowns warned, median 49.6 h ahead | [m5-detection-backtest.json](../../evidence/benchmarks/m5-detection-backtest.json) |
| PR-AUC 0.742 vs 0.508 | [m8-model-vs-baseline.json](../../evidence/benchmarks/m8-model-vs-baseline.json) |
| Radar: U0100 on EV7V4 2026.7 flagged (8–15× baseline in the rehearsal); 0 false signals | [m7-radar-backtest.json](../../evidence/benchmarks/m7-radar-backtest.json), [m7-radar-live.json](../../evidence/benchmarks/m7-radar-live.json) |
| 21.5 US cents a mile (ATRI, as reported by trade press) | [Solution Document §2.2](../solution-document/solution-document.md#22-evidence--validation) |
| Demo timings | [m18-rehearsal-timeline.json](../../evidence/demo/m18-rehearsal-timeline.json) |

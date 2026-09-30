"""Load test the running pipeline at a given fleet size and event rate (M14).

    uv run python scripts/load_test.py --name 100k --vehicles 100000 --rate 10000 \\
        --duration 300 [--burst 3:120:60] [--normalizers 2]

Needs `make up-obs` (Prometheus) and the pipeline images. For each scenario it:
  1. seeds PostgreSQL with exactly --vehicles (the normalizer drops unknown VINs), and
     restarts the consumers so they reload the vehicle registry;
  2. (re)starts the simulator with the requested rate and optional burst
     (MULTIPLIER:START_S:DURATION_S);
  3. every --interval seconds samples Prometheus (produced, per-stage consumed rates,
     seconds behind per consumer group, alert latency) and `docker stats`;
  4. writes evidence/load-tests/m14-<name>.json: the time series and a summary over the
     steady window (after --warmup).
"Sustained" means every consumer group ends less than 60 s behind (the SLO) and its
backlog is not growing over the last minute.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evidence/load-tests"
PROM = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
STAGES = ("normalizer", "detector", "sink", "radar")
QUERIES = {
    "target_ev_s": "sum(prognos_sim_target_events_per_second)",
    "delivered_ev_s": "sum(rate(prognos_sim_messages_delivered_total[30s]))",
    **{f"{s}_msg_s": f'sum(rate(prognos_stream_consumed_total{{service="{s}"}}[30s]))'
       for s in STAGES},
    **{f"{s}_lag": f'sum(prognos_stream_consumer_lag{{service="{s}"}})' for s in STAGES},
    **{f"{s}_seconds_behind": f'max(prognos:stream_seconds_behind{{service="{s}"}})'
       for s in STAGES},
    "alerts_per_min": "sum(rate(prognos_detector_alerts_total[1m])) * 60",
}  # fmt: skip


def compose(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["docker", "compose", "--profile", "pipeline", *args]
    subprocess.run(cmd, check=True, capture_output=True, env={**os.environ, **(env or {})})  # noqa: S603


def dsn() -> str:
    e = os.environ
    return make_conninfo(
        host=e.get("POSTGRES_SEED_HOST", "localhost"),
        port=e.get("POSTGRES_HOST_PORT", "5432"),
        dbname=e.get("POSTGRES_DB", "prognos"),
        user=e.get("POSTGRES_USER", "prognos"),
        password=e.get("POSTGRES_PASSWORD", ""),
    )


def prom(client: httpx.Client, query: str, at: float | None = None) -> float | None:
    params: dict[str, Any] = {"query": query}
    if at:
        params["time"] = at
    r = client.get(f"{PROM}/api/v1/query", params=params)
    result = r.json()["data"]["result"]
    return float(result[0]["value"][1]) if result else None


def docker_stats() -> dict[str, dict[str, float]]:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],  # noqa: S607
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    stats: dict[str, dict[str, float]] = {}
    for line in out.splitlines():
        s = json.loads(line)
        name = s["Name"].removeprefix("prognos-")
        mem = s["MemUsage"].split("/")[0].strip()
        value, unit = float(mem.rstrip("KMGiB")), mem.lstrip("0123456789.")
        mib = value * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "B": 1 / 1048576}[unit]
        stats[name] = {"cpu_pct": float(s["CPUPerc"].rstrip("%")), "mem_mib": round(mib, 1)}
    return stats


def prepare(vehicles: int, normalizers: int) -> None:
    with psycopg.connect(dsn()) as conn:
        have = conn.execute("SELECT count(*) FROM vehicles").fetchone()[0]  # type: ignore[index]
    if have != vehicles:
        seed = [str(ROOT / "scripts/seed.sh"), "--vehicles", str(vehicles), "--reset"]
        subprocess.run(seed, check=True, capture_output=True)  # noqa: S603
    # consumers reload the registry on start; simulator stopped first so lag starts at 0
    compose("stop", "simulator")
    compose("up", "-d", "--no-deps", "--no-build", "--force-recreate",
            "--scale", f"normalizer={normalizers}", "normalizer", "detector", "sink", "radar",
            env={"NORMALIZER_REPLICAS": str(normalizers)})  # fmt: skip


def summarise(samples: list[dict[str, Any]], warmup: float, client: httpx.Client,
              start: float, end: float) -> dict[str, Any]:  # fmt: skip
    steady = [s for s in samples if s["t"] >= warmup]
    last_min = [s for s in samples if s["t"] >= samples[-1]["t"] - 60]

    def mean(key: str, rows: list[dict[str, Any]] = steady) -> float | None:
        vals = [s[key] for s in rows if s.get(key) is not None]
        return round(statistics.fmean(vals), 1) if vals else None

    stages: dict[str, Any] = {}
    for s in STAGES:
        lags = [x[f"{s}_lag"] for x in last_min if x.get(f"{s}_lag") is not None]
        behind = [x[f"{s}_seconds_behind"] for x in steady
                  if x.get(f"{s}_seconds_behind") is not None]  # fmt: skip
        growing = len(lags) >= 2 and lags[-1] > lags[0] * 1.2 + 1000
        stages[s] = {"mean_msg_s": mean(f"{s}_msg_s"),
                     "max_seconds_behind": round(max(behind), 1) if behind else None,
                     "final_seconds_behind": round(behind[-1], 1) if behind else None,
                     "final_lag": lags[-1] if lags else None, "backlog_growing": growing,
                     "sustained": bool(behind) and behind[-1] < 60 and not growing}  # fmt: skip
    window = f"{int(end - (start + warmup))}s"
    latency = {
        q: prom(client, f"histogram_quantile({q}, sum by (le) (increase("
                        f"prognos_detector_alert_latency_seconds_bucket[{window}])))", end)
        for q in (0.5, 0.95, 0.99)
    }  # fmt: skip
    cpu: dict[str, list[float]] = {}
    mem: dict[str, list[float]] = {}
    for sample in steady:
        for name, st in sample.get("docker", {}).items():
            cpu.setdefault(name, []).append(st["cpu_pct"])
            mem.setdefault(name, []).append(st["mem_mib"])
    resources = {n: {"mean_cpu_pct": round(statistics.fmean(cpu[n]), 1),
                     "max_mem_mib": max(mem[n])} for n in cpu}  # fmt: skip
    return {
        "mean_target_ev_s": mean("target_ev_s"), "mean_delivered_ev_s": mean("delivered_ev_s"),
        "stages": stages, "all_sustained": all(v["sustained"] for v in stages.values()),
        "alert_latency_s": {f"p{int(q * 100)}": (round(v, 3) if v is not None else None)
                            for q, v in latency.items()},
        "containers": resources,
        "total_cpu_pct_mean": round(sum(r["mean_cpu_pct"] for r in resources.values()), 1),
        "total_mem_mib_max": round(sum(r["max_mem_mib"] for r in resources.values()), 1),
    }  # fmt: skip


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--name", required=True)
    p.add_argument("--vehicles", type=int, required=True)
    p.add_argument("--rate", type=float, required=True, help="simulator events/s")
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--warmup", type=float, default=60)
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--normalizers", type=int, default=2)
    p.add_argument("--burst", help="MULTIPLIER:START_S:DURATION_S, e.g. 3:120:60")
    p.add_argument("--keep-running", action="store_true", help="leave the simulator running")
    args = p.parse_args()

    prepare(args.vehicles, args.normalizers)
    mult, bstart, bdur = (args.burst or "1:-1:0").split(":")
    compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "simulator", env={
        "VEHICLE_COUNT": str(args.vehicles), "EVENTS_PER_SECOND": str(args.rate),
        "BURST_MULTIPLIER": mult, "BURST_START_SECONDS": bstart,
        "BURST_DURATION_SECONDS": bdur,
    })  # fmt: skip
    start = time.time()
    samples: list[dict[str, Any]] = []
    with httpx.Client(timeout=10) as client:
        while (elapsed := time.time() - start) < args.duration:
            row: dict[str, Any] = {"t": round(elapsed, 1)}
            for key, q in QUERIES.items():
                v = prom(client, q)
                row[key] = round(v, 2) if v is not None else None
            row["docker"] = docker_stats()
            samples.append(row)
            print(f"t={row['t']:>6.0f}s target={row['target_ev_s']} delivered="
                  f"{row['delivered_ev_s']} norm={row['normalizer_msg_s']} "
                  f"det={row['detector_msg_s']} behind(n/d)={row['normalizer_seconds_behind']}/"
                  f"{row['detector_seconds_behind']}", flush=True)  # fmt: skip
            time.sleep(max(0.0, args.interval - (time.time() - start - elapsed)))
        end = time.time()
        summary = summarise(samples, args.warmup, client, start, end)
    if not args.keep_running:
        compose("stop", "simulator")
    doc = {
        "measured_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "hardware": "x86_64 Linux container, 4 vCPU shared by every service (simulator, "
                    "Kafka, consumers, databases, Prometheus); not the target Mac",
        "scenario": {"name": args.name, "vehicles": args.vehicles, "rate_ev_s": args.rate,
                     "duration_s": args.duration, "warmup_s": args.warmup,
                     "normalizers": args.normalizers, "burst": args.burst},
        "summary": summary, "samples": samples,
    }  # fmt: skip
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"m14-{args.name}.json"
    path.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

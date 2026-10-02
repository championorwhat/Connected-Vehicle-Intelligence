"""Record what the dashboard shows from a running demo stack, for the static GitHub Pages build.

    make pipeline-demo users                      # let it run until the scripted failures appear
    make demo-snapshot                            # writes apps/web/demo/snapshot.json

Signs in as the fleet-manager demo user and saves the API's own responses: fleet summary,
both at-risk rankings, every alert and work order (up to a cap), fleet signals, and the
detail of every vehicle those mention. `VITE_DEMO=1 npm run build` bundles the file and
serves it in place of the API, so every number on the Pages site came from a real run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import httpx

OUT = Path(__file__).resolve().parents[1] / "apps/web/demo/snapshot.json"
EMAIL = "fleet_manager@demo.prognos.local"


def get(api: httpx.Client, path: str, **params: Any) -> Any:
    res = api.get(path, params={k: v for k, v in params.items() if v is not None})
    res.raise_for_status()
    return res.json()


def pages(api: httpx.Client, path: str, cap: int) -> list[Any]:
    items: list[Any] = []
    cursor = None
    while len(items) < cap:
        page = get(api, path, limit=200, cursor=cursor)
        items += page["items"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    return items[:cap]


def sign_in(base: str, password: str) -> httpx.Client:
    res = httpx.post(f"{base}/v1/auth/token", data={"username": EMAIL, "password": password})
    res.raise_for_status()
    token = res.json()["access_token"]
    return httpx.Client(base_url=base, timeout=30, headers={"Authorization": f"Bearer {token}"})


def capture(api: httpx.Client, cap: int, vehicle_cap: int) -> dict[str, Any]:
    at_risk = {
        source: get(api, "/v1/vehicles/at-risk", source=source, limit=25)
        for source in ("rules", "model")
    }
    alerts = pages(api, "/v1/alerts", cap)
    work_orders = pages(api, "/v1/work-orders", cap)
    ids: list[str] = []
    for vid in (
        [v["vehicle_id"] for r in at_risk.values() for v in r["items"]]
        + [a["vehicle_id"] for a in alerts]
        + [w["vehicle_id"] for w in work_orders]
    ):
        if vid not in ids:
            ids.append(vid)
    return {
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "me": get(api, "/v1/auth/me"),
        "summary": get(api, "/v1/fleet/summary"),
        "at_risk": at_risk,
        "alerts": alerts,
        "work_orders": work_orders,
        "signals": get(api, "/v1/fleet/signals"),
        "vehicles": {vid: get(api, f"/v1/vehicles/{vid}") for vid in ids[:vehicle_cap]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    port = os.environ.get("API_HOST_PORT", "8000")
    parser.add_argument("--api", default=f"http://localhost:{port}")
    parser.add_argument("--cap", type=int, default=600, help="most alerts / work orders to keep")
    parser.add_argument("--vehicle-cap", type=int, default=400, help="most vehicle details to keep")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    password = os.environ.get("DEMO_USER_PASSWORD")
    if not password:
        raise SystemExit("set DEMO_USER_PASSWORD (the password `make users` used)")
    with sign_in(args.api, password) as api:
        snapshot = capture(api, args.cap, args.vehicle_cap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, separators=(",", ":"), sort_keys=True) + "\n")
    counts = {k: len(snapshot[k]) for k in ("alerts", "work_orders", "vehicles")}
    print(f"wrote {args.output}: {counts}, {len(snapshot['signals']['items'])} signals")


if __name__ == "__main__":
    main()

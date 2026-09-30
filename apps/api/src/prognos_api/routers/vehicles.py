"""Vehicles: list (keyset), detail, at-risk ranking. Live state and risk come from Redis."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Query
from redis.exceptions import RedisError

from prognos_api import pagination
from prognos_api.deps import DbDep, StateDep, require
from prognos_api.errors import ApiError
from prognos_api.security import Principal

router = APIRouter(tags=["vehicles"])
VehicleReader = Annotated[Principal, Depends(require("vehicle:read"))]

_COLUMNS = """
    v.vehicle_id::text AS vehicle_id, v.vin::text AS vin, v.model_code::text AS model_code,
    m.display_name AS model_name, m.oem_code AS oem, m.powertrain, v.model_year,
    v.firmware_version, v.status, v.fleet_id::text AS fleet_id,
    v.home_workshop_id::text AS home_workshop_id
"""


def mask_location(state: dict[str, Any], principal: Principal) -> dict[str, Any]:
    """Without location:read_precise, positions are rounded to ~1 km (2 decimals)."""
    if principal.can("location:read_precise"):
        return {**state, "location_precision": "precise"}
    out = dict(state)
    for key in ("latitude", "longitude"):
        if out.get(key) is not None:
            out[key] = round(float(out[key]), 2)
    out["location_precision"] = "approx_1km"
    return out


async def live(st: Any, principal: Principal, vehicle_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Latest state + model risk per vehicle; empty when Redis is unavailable (AP data)."""
    if not vehicle_ids:
        return {}
    keys = [k for vid in vehicle_ids for k in (f"veh:{vid}", f"veh:{vid}:risk")]
    try:
        raw = await st.redis.mget(keys)
    except RedisError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i, vid in enumerate(vehicle_ids):
        state_raw, risk_raw = raw[2 * i], raw[2 * i + 1]
        entry: dict[str, Any] = {}
        if state_raw:
            s = orjson.loads(state_raw)
            entry["live"] = mask_location({
                "health_score": s.get("health_score"),
                "as_of": s.get("as_of"),
                "latitude": s.get("latitude"), "longitude": s.get("longitude"),
                "speed_kmh": s.get("speed_kmh"), "ignition_on": s.get("ignition_on"),
                "active_alerts": s.get("active_alerts", []),
                "indicators": s.get("indicators", {}),
            }, principal)  # fmt: skip
        if risk_raw:
            entry["risk"] = orjson.loads(risk_raw)
        out[vid] = entry
    return out


@router.get("/v1/vehicles")
async def list_vehicles(
    st: StateDep, conn: DbDep, principal: VehicleReader,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = 50,
    status: Annotated[str | None, Query(pattern="^(active|in_workshop|retired)$")] = None,
    model_code: Annotated[str | None, Query(pattern="^[A-HJ-NPR-Z0-9]{5}$")] = None,
) -> dict[str, Any]:  # fmt: skip
    after = pagination.decode(cursor, 1)
    sql = f"SELECT {_COLUMNS} FROM vehicles v JOIN vehicle_models m USING (model_code) " \
          "WHERE v.tenant_id = %(tid)s"  # fmt: skip
    params: dict[str, Any] = {"tid": principal.tenant_id, "limit": limit + 1}
    if status:
        sql += " AND v.status = %(status)s"
        params["status"] = status
    if model_code:
        sql += " AND v.model_code = %(model)s"
        params["model"] = model_code
    if after:
        sql += " AND v.vehicle_id > %(after)s"
        params["after"] = after[0]
    sql += " ORDER BY v.vehicle_id LIMIT %(limit)s"
    rows = await (await conn.execute(sql, params)).fetchall()
    result = pagination.page(rows, limit, ["vehicle_id"])
    extra = await live(st, principal, [r["vehicle_id"] for r in result["items"]])
    result["items"] = [r | extra.get(r["vehicle_id"], {}) for r in result["items"]]
    return result


@router.get("/v1/vehicles/at-risk")
async def at_risk(
    st: StateDep, conn: DbDep, principal: VehicleReader,
    limit: Annotated[int, Query(ge=1, le=pagination.MAX_LIMIT)] = 25,
    source: Annotated[str | None, Query(pattern="^(rules|model)$")] = None,
) -> dict[str, Any]:  # fmt: skip
    """Most at-risk vehicles, by rules health (default) or by the live model's probability.

    The model runs in shadow mode (ADR-008): ask for ?source=model to see its ranking.
    If model scores are missing the rules ranking is returned and `fallback` is true.
    """
    tid = principal.tenant_id
    wanted = source or st.settings.risk_source
    ranked: Any = []
    try:
        if wanted == "model":
            ranked = await st.redis.zrevrange(f"tenant:{tid}:risk", 0, limit - 1, withscores=True)
        used = f"model:{st.settings.model_version}" if ranked else "rules:health_score"
        if not ranked:
            ranked = await st.redis.zrange(f"tenant:{tid}:health", 0, limit - 1, withscores=True)
    except RedisError as exc:
        raise ApiError(503, "live risk ranking unavailable", code="live-state-down") from exc
    scores = {(v.decode() if isinstance(v, bytes) else str(v)): float(s) for v, s in ranked}
    ids = list(scores)
    meta = {"source": used, "requested": wanted, "fallback": not used.startswith(wanted)}
    if not ids:
        return {**meta, "items": []}
    rows = await (await conn.execute(
        f"SELECT {_COLUMNS} FROM vehicles v JOIN vehicle_models m USING (model_code) "
        "WHERE v.tenant_id = %s AND v.vehicle_id = ANY(%s::uuid[])", (tid, ids),
    )).fetchall()  # fmt: skip
    by_id = {r["vehicle_id"]: r for r in rows}
    extra = await live(st, principal, ids)
    items = [
        by_id[vid] | {"score": round(scores[vid], 4)} | extra.get(vid, {})
        for vid in ids
        if vid in by_id  # ignore stale entries for vehicles no longer in this tenant
    ]
    return {**meta, "items": items}


@router.get("/v1/vehicles/{vehicle_id}")
async def get_vehicle(
    vehicle_id: uuid.UUID, st: StateDep, conn: DbDep, principal: VehicleReader
) -> dict[str, Any]:
    row = await (await conn.execute(
        f"SELECT {_COLUMNS}, (SELECT count(*) FROM alerts a WHERE a.vehicle_id = v.vehicle_id"
        " AND a.status IN ('open', 'acknowledged')) AS open_alerts"
        " FROM vehicles v JOIN vehicle_models m USING (model_code)"
        " WHERE v.vehicle_id = %s AND v.tenant_id = %s", (vehicle_id, principal.tenant_id),
    )).fetchone()  # fmt: skip
    if row is None:  # other tenants' vehicles are indistinguishable from missing ones
        raise ApiError(404, "vehicle not found", code="not-found")
    return row | (await live(st, principal, [row["vehicle_id"]])).get(row["vehicle_id"], {})

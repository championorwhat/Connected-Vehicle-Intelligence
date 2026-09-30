"""Planner against real PostgreSQL: proposals, audit trail, idempotency, no fabricated money."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from prognos_stream.planner import Calibration
from prognos_stream.planner_main import plan_once

CAL = Calibration.from_dict(
    {
        "version": "itest",
        "horizon_hours": 168,
        "fallback": {"p_failure": 0.5, "hours_to_failure_p10": None},
        "rules": {
            "COOLANT_OVERHEAT": {"p_failure": 0.95, "hours_to_failure_p10": 12.0},
            "MISFIRE_ROUGHNESS": {"p_failure": 0.7, "hours_to_failure_p10": 48.0},
        },
    }
)


def test_planner_proposes_once_with_audit_and_no_placeholder_money(seeded_dsn: str) -> None:
    with psycopg.connect(seeded_dsn, autocommit=True) as conn:
        tenant = conn.execute(
            "SELECT tenant_id FROM tenants ORDER BY tenant_id DESC LIMIT 1"
        ).fetchone()[0]  # type: ignore[index]
        vehicles = [
            r[0]
            for r in conn.execute(
                "SELECT v.vehicle_id FROM vehicles v JOIN vehicle_models m USING (model_code)"
                " WHERE v.tenant_id = %s AND m.powertrain = 'ICE' ORDER BY v.vehicle_id LIMIT 3",
                (tenant,),
            )
        ]
        assert len(vehicles) == 3
        rules = [("COOLANT_OVERHEAT", "critical", "COOLING_FAILURE"),
                 ("MISFIRE_ROUGHNESS", "warning", "IGNITION_MISFIRE"),
                 ("COOLANT_OVERHEAT", "critical", "COOLING_FAILURE")]  # fmt: skip
        for i, (vid, (rule, sev, mode)) in enumerate(zip(vehicles, rules, strict=True)):
            conn.execute(
                "INSERT INTO alerts (tenant_id, vehicle_id, fingerprint, rule_code, severity,"
                " failure_mode, event_ts, details) VALUES (%s, %s, %s, %s, %s, %s,"
                " now() - interval '1 hour', %s)",
                (tenant, vid, f"planner-itest-{i}", rule, sev, mode, Jsonb({})),
            )

        first = plan_once(conn, CAL)
        rows = conn.execute(
            "SELECT vehicle_id, status, failure_probability::float8, expected_cost_avoided,"
            " model_version, scheduled_for - current_date, source_alert_id IS NOT NULL"
            " FROM work_orders WHERE vehicle_id = ANY(%s)",
            (vehicles,),
        ).fetchall()
        assert len(rows) == 3
        by_vehicle = {r[0]: r for r in rows}
        assert all(r[1] == "proposed" for r in rows)
        assert all(r[3] is None for r in rows), "placeholder costs must not become money"
        assert all(r[4] == "rules-calibrated-itest" for r in rows)
        assert all(0 <= r[5] < 7 and r[6] for r in rows)
        assert by_vehicle[vehicles[0]][2] == 0.95
        assert first["work_orders_proposed"] >= 3
        assert first["value_basis"] == ["risk_only"]

        audit = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action = 'work_order.propose'"
            " AND actor_type = 'system' AND resource_id IN"
            " (SELECT work_order_id::text FROM work_orders WHERE vehicle_id = ANY(%s))",
            (vehicles,),
        ).fetchone()[0]  # type: ignore[index]
        assert audit == 3

        second = plan_once(conn, CAL)  # re-run: every alert already has an active order
        assert second["work_orders_proposed"] == 0
        count = conn.execute(
            "SELECT count(*) FROM work_orders WHERE vehicle_id = ANY(%s)", (vehicles,)
        ).fetchone()[0]  # type: ignore[index]
        assert count == 3

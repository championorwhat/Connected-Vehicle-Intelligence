import { describe, expect, it, vi } from "vitest";

import type { Alert, Page, Summary, Vehicle, WorkOrder } from "../../src/api/client";

const CAPTURED = "2026-01-01T12:00:00+00:00";

vi.mock("../../demo/snapshot.json", () => ({
  default: {
    captured_at: CAPTURED,
    me: { user_id: "u", tenant_id: "t", roles: ["fleet_manager"], permissions: ["alert:ack"] },
    summary: {
      vehicles: 10, vehicles_in_workshop: 0, open_critical_alerts: 1, open_warning_alerts: 1, acknowledged_alerts: 0,
      work_orders_proposed: 1, work_orders_scheduled: 0, work_orders_in_progress: 0,
    },
    at_risk: {
      rules: { source: "rules:health_score", requested: "rules", fallback: false, items: [{ vehicle_id: "v1" }, { vehicle_id: "v2" }] },
      model: { source: "rules:health_score", requested: "model", fallback: true, items: [] },
    },
    alerts: [
      { alert_id: 2, vehicle_id: "v1", severity: "critical", status: "open", detected_at: "2026-01-01T11:55:00+00:00", details: {} },
      { alert_id: 1, vehicle_id: "v2", severity: "warning", status: "open", detected_at: "2026-01-01T11:50:00+00:00", details: {} },
    ],
    work_orders: [
      { work_order_id: "w1", vehicle_id: "v1", status: "proposed", scheduled_for: "2026-01-03", created_at: CAPTURED },
    ],
    signals: { items: [] },
    vehicles: { v1: { vehicle_id: "v1", live: { as_of: "2026-01-01T11:59:00+00:00" } } },
  },
}));

const { demoRequest, demoSession } = await import("../../src/api/demo");

describe("demo mode responder", () => {
  it("signs in as the recorded user", () => {
    expect(demoSession.roles).toEqual(["fleet_manager"]);
  });

  it("moves recorded times forward to now, keeping their spacing", async () => {
    const v = await demoRequest<Vehicle>("/v1/vehicles/v1");
    const age = Date.now() - Date.parse(v.live!.as_of!);
    expect(age).toBeGreaterThanOrEqual(60_000);
    expect(age).toBeLessThan(65_000);
  });

  it("filters and pages alerts like the API", async () => {
    const critical = await demoRequest<Page<Alert>>("/v1/alerts?severity=critical&limit=50");
    expect(critical.items.map((a) => a.alert_id)).toEqual([2]);
    const first = await demoRequest<Page<Alert>>("/v1/alerts?limit=1");
    expect(first.next_cursor).toBe("1");
    const second = await demoRequest<Page<Alert>>(`/v1/alerts?limit=1&cursor=${first.next_cursor}`);
    expect(second.items.map((a) => a.alert_id)).toEqual([1]);
    expect(second.next_cursor).toBeNull();
  });

  it("acknowledges an alert once and updates the summary", async () => {
    const a = await demoRequest<Alert>("/v1/alerts/2/acknowledge", { method: "POST" });
    expect(a.status).toBe("acknowledged");
    const s = await demoRequest<Summary>("/v1/fleet/summary");
    expect([s.open_critical_alerts, s.acknowledged_alerts]).toEqual([0, 1]);
    await expect(demoRequest("/v1/alerts/2/acknowledge", { method: "POST" })).rejects.toMatchObject({ status: 409 });
  });

  it("applies work-order transitions with the API's rules", async () => {
    await expect(demoRequest("/v1/work-orders/w1/start", { method: "POST" })).rejects.toMatchObject({ status: 409 });
    const w = await demoRequest<WorkOrder>("/v1/work-orders/w1/schedule", {
      method: "POST", body: JSON.stringify({ scheduled_for: "2030-05-01" }),
    });
    expect([w.status, w.scheduled_for]).toEqual(["scheduled", "2030-05-01"]);
    const s = await demoRequest<Summary>("/v1/fleet/summary");
    expect([s.work_orders_proposed, s.work_orders_scheduled]).toEqual([0, 1]);
  });

  it("says when something is not in the snapshot", async () => {
    await expect(demoRequest("/v1/vehicles/nope")).rejects.toMatchObject({ status: 404 });
  });
});

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { signIn, signOut, type Alert, type AtRisk, type WorkOrder } from "../../src/api/client";
import { ago, alertLabel, alertShort, failureLabel, featureLabel, healthWord, pct } from "../../src/format";
import { Login } from "../../src/pages/Login";
import { Overview, SourceNote } from "../../src/pages/Overview";
import { advice } from "../../src/pages/VehicleDetail";
import { actionsFor, basisLabel } from "../../src/pages/WorkOrders";

vi.mock("../../src/components/FleetMap", () => ({
  FleetMap: () => <div data-testid="map" />,
  riskBand: () => "low",
}));

type Route = (url: string, init?: RequestInit) => { status: number; body: unknown } | undefined;

function mockFetch(route: Route) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const hit = route(String(url), init) ?? { status: 404, body: { status: 404, detail: "not found" } };
    return new Response(JSON.stringify(hit.body), { status: hit.status });
  }));
}

async function loginAs(permissions: string[]) {
  mockFetch((url) =>
    url.startsWith("/v1/auth/token")
      ? { status: 200, body: { access_token: "t", token_type: "bearer", expires_in: 900 } }
      : { status: 200, body: { user_id: "u", tenant_id: "t1", roles: ["x"], permissions } },
  );
  await signIn("a@b.c", "pw");
}

const wo = (status: WorkOrder["status"]): WorkOrder => ({
  work_order_id: "w1", vehicle_id: "v1", workshop_id: "s1", failure_mode: "COOLING_FAILURE",
  status, failure_probability: 0.9, expected_cost_avoided: null, model_version: "rules-calibrated-v1",
  scheduled_for: null, created_at: new Date().toISOString(), outcome: null,
});

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  cleanup();
  signOut();
  vi.unstubAllGlobals();
});

describe("format", () => {
  it("formats probabilities, ages and model features for people", () => {
    expect(pct(0.934)).toBe("93%");
    expect(pct(null)).toBe("–");
    expect(ago(new Date(Date.now() - 90_000).toISOString())).toBe("1m ago");
    expect(featureLabel("lv_min_long")).toBe("lowest 12 V battery voltage");
    expect(featureLabel("some_new_feature")).toBe("Some new feature");
  });

  it("puts alert codes, failure types and health scores into plain words", () => {
    expect(alertLabel("COOLANT_OVERHEAT")).toBe("Engine overheating");
    expect(alertLabel("DTC_P0301")).toBe("Fault code P0301: Misfire in cylinder 1");
    expect(alertLabel("DTC_B9999")).toBe("Fault code B9999");
    expect(alertLabel("SOMETHING_NEW")).toBe("Something new");
    expect(alertShort("DTC_P0301")).toBe("Misfire in cylinder 1");
    expect(alertShort("TYRE_SLOW_LEAK")).toBe("Tyre slowly losing pressure");
    expect(failureLabel("HV_BATTERY_THERMAL")).toBe("EV battery problem");
    expect(failureLabel(null)).toBe("–");
    expect(healthWord(30)).toBe("Needs attention");
    expect(healthWord(85)).toBe("Keep an eye on");
    expect(healthWord(100)).toBe("Healthy");
    expect(healthWord(undefined)).toBe("No data yet");
    expect(basisLabel("rules-calibrated-v1")).toBe("Alert rules");
    expect(basisLabel(null)).toBe("Created by hand");
  });

  it("tells the user what to do about a vehicle", () => {
    const alert = (rule_code: string, severity: Alert["severity"], status = "open") =>
      ({ rule_code, severity, status }) as Alert;
    expect(advice([])).toBe("No open problems.");
    expect(advice([alert("DTC_P0118", "warning")])).toMatch(/^Early warning/);
    expect(advice([alert("COOLANT_OVERHEAT", "critical")])).toMatch(/^Critical problem/);
    expect(advice([alert("VEHICLE_BREAKDOWN", "critical")])).toMatch(/broken down/);
    expect(advice([alert("COOLANT_OVERHEAT", "critical", "resolved")])).toBe("No open problems.");
  });
});

describe("work order actions follow role and state", () => {
  it("fleet manager schedules and cancels proposals but cannot complete", async () => {
    await loginAs(["work_order:read", "work_order:write"]);
    expect(actionsFor(wo("proposed"))).toEqual(["schedule", "cancel"]);
    expect(actionsFor(wo("completed"))).toEqual([]);
  });
  it("technician starts and completes scheduled work only", async () => {
    await loginAs(["work_order:read", "work_order:complete"]);
    expect(actionsFor(wo("proposed"))).toEqual([]);
    expect(actionsFor(wo("scheduled"))).toEqual(["start", "complete"]);
    expect(actionsFor(wo("in_progress"))).toEqual(["complete"]);
  });
});

describe("shadow mode is explicit", () => {
  const base: AtRisk = { source: "model:failure-7d-v3", requested: "model", fallback: false, items: [] };
  it("labels the model ranking as shadow", () => {
    render(<SourceNote data={base} />);
    expect(screen.getByRole("status").textContent).toMatch(/shadow mode/);
  });
  it("says when it fell back to the rules", () => {
    render(<SourceNote data={{ ...base, source: "rules:health_score", fallback: true }} />);
    expect(screen.getByRole("status").textContent).toMatch(/showing the rules ranking/);
  });
});

describe("login", () => {
  it("shows the API's error message on bad credentials", async () => {
    mockFetch(() => ({ status: 401, body: { status: 401, title: "Unauthorized", detail: "invalid credentials" } }));
    render(<Login />);
    await userEvent.type(screen.getByLabelText("Email"), "x@y.z");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe("invalid credentials");
  });
});

describe("overview", () => {
  it("renders summary tiles and the at-risk list", async () => {
    await loginAs(["fleet:read", "vehicle:read"]);
    mockFetch((url) => {
      if (url.startsWith("/v1/fleet/summary"))
        return { status: 200, body: { vehicles: 5000, open_critical_alerts: 3, open_warning_alerts: 7,
          acknowledged_alerts: 0, vehicles_in_workshop: 0, work_orders_proposed: 2,
          work_orders_scheduled: 1, work_orders_in_progress: 0 } };
      if (url.startsWith("/v1/vehicles/at-risk"))
        return { status: 200, body: { source: "rules:health_score", requested: "rules", fallback: false,
          items: [{ vehicle_id: "v1", vin: "PG1CT1A59RC000001", model_code: "CT1A5",
            model_name: "Orion Cargo Van", oem: "ORION", powertrain: "ICE", model_year: 2024,
            firmware_version: "3.1.0", status: "active", score: 20,
            live: { health_score: 20, as_of: null, latitude: null, longitude: null, speed_kmh: 0,
              ignition_on: false, active_alerts: ["COOLANT_OVERHEAT"], indicators: {},
              location_precision: "precise" } }] } };
      return undefined;
    });
    render(<MemoryRouter><Overview /></MemoryRouter>);
    expect(await screen.findByText("5,000")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("PG1CT1A59RC000001")).toBeTruthy());
    expect(screen.getByText("Engine overheating")).toBeTruthy();
    expect(screen.getByText("Needs attention")).toBeTruthy();
  });
});

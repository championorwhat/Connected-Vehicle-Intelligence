import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { signIn, signOut, type AtRisk, type WorkOrder } from "../../src/api/client";
import { ago, featureLabel, pct } from "../../src/format";
import { Login } from "../../src/pages/Login";
import { Overview, SourceNote } from "../../src/pages/Overview";
import { actionsFor } from "../../src/pages/WorkOrders";

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
    expect(screen.getByText("COOLANT_OVERHEAT")).toBeTruthy();
  });
});

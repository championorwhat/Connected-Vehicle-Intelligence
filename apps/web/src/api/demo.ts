// Demo mode (GitHub Pages build, `VITE_DEMO=1`): answers the dashboard's API calls from a
// snapshot recorded off a real `make pipeline-demo` run by scripts/capture_demo_snapshot.py.
//
// Nothing here invents data. Timestamps are moved forward by (now - captured_at) so "5 min
// ago" reads as it did when the snapshot was taken; acknowledging alerts and moving work
// orders change this tab's copy only, and the banner says both.

import raw from "../../demo/snapshot.json";
import type { Alert, AtRisk, Problem, Session, Signal, Summary, Vehicle, WorkOrder } from "./client";

interface Snapshot {
  captured_at: string;
  me: { user_id: string; tenant_id: string | null; roles: string[]; permissions: string[] };
  summary: Summary;
  at_risk: Record<"rules" | "model", AtRisk>;
  alerts: Alert[];
  work_orders: WorkOrder[];
  signals: { items: Signal[] };
  vehicles: Record<string, Vehicle>;
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;
const DAY = 86_400_000;

function shifted<T>(value: T, ms: number): T {
  if (typeof value === "string" && ISO.test(value)) return new Date(Date.parse(value) + ms).toISOString() as T;
  if (Array.isArray(value)) return value.map((v) => shifted(v, ms)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      // Dates without a time (booking days) move by whole days.
      out[k] = k === "scheduled_for" && typeof v === "string" && !ISO.test(v)
        ? new Date(Date.parse(v) + Math.round(ms / DAY) * DAY).toISOString().slice(0, 10)
        : shifted(v, ms);
    }
    return out as T;
  }
  return value;
}

const source = raw as unknown as Snapshot;
export const capturedAt = source.captured_at;
const data: Snapshot = shifted(source, Math.max(0, Date.now() - Date.parse(source.captured_at)));

export const demoSession: Session = {
  token: "demo",
  userId: data.me.user_id,
  tenantId: data.me.tenant_id,
  roles: data.me.roles,
  permissions: data.me.permissions,
};

class DemoProblem extends Error {
  constructor(public problem: Problem) {
    super(problem.detail);
  }
}

const fail = (status: number, title: string, detail: string): never => {
  throw new DemoProblem({ status, title, detail });
};

function page<T>(items: T[], params: URLSearchParams): { items: T[]; next_cursor: string | null } {
  const limit = Number(params.get("limit") ?? 50);
  const start = Number(params.get("cursor") ?? 0);
  const end = start + limit;
  return { items: items.slice(start, end), next_cursor: end < items.length ? String(end) : null };
}

const nowIso = () => new Date().toISOString();

const TRANSITIONS: Record<string, [WorkOrder["status"][], WorkOrder["status"]]> = {
  schedule: [["proposed", "scheduled"], "scheduled"],
  cancel: [["proposed", "scheduled"], "cancelled"],
  start: [["scheduled"], "in_progress"],
  complete: [["scheduled", "in_progress"], "completed"],
};

const SUMMARY_KEY: Partial<Record<WorkOrder["status"], keyof Summary>> = {
  proposed: "work_orders_proposed",
  scheduled: "work_orders_scheduled",
  in_progress: "work_orders_in_progress",
};

function route(method: string, path: string, params: URLSearchParams, body: unknown): unknown {
  let m: RegExpMatchArray | null;
  if (method === "GET" && path === "/v1/fleet/summary") return data.summary;
  if (method === "GET" && path === "/v1/fleet/signals") return data.signals;
  if (method === "GET" && path === "/v1/vehicles/at-risk") {
    const r = data.at_risk[params.get("source") === "model" ? "model" : "rules"];
    return { ...r, items: r.items.slice(0, Number(params.get("limit") ?? 25)) };
  }
  if (method === "GET" && (m = path.match(/^\/v1\/vehicles\/([^/]+)$/)))
    return data.vehicles[decodeURIComponent(m[1] ?? "")]
      ?? fail(404, "Not Found", "This vehicle is not in the demo snapshot. Run the full stack to see every vehicle.");
  if (method === "GET" && path === "/v1/alerts") {
    const [status, severity, vid] = ["status", "severity", "vehicle_id"].map((k) => params.get(k));
    return page(data.alerts.filter((a) =>
      (!status || a.status === status) && (!severity || a.severity === severity) && (!vid || a.vehicle_id === vid)),
    params);
  }
  if (method === "POST" && (m = path.match(/^\/v1\/alerts\/(\d+)\/acknowledge$/))) {
    const a = data.alerts.find((x) => x.alert_id === Number(m![1])) ?? fail(404, "Not Found", "No such alert");
    if (a.status !== "open") fail(409, "Conflict", `cannot acknowledge a ${a.status} alert`);
    a.status = "acknowledged";
    a.acknowledged_at = nowIso();
    data.summary.acknowledged_alerts += 1;
    if (a.severity === "critical") data.summary.open_critical_alerts -= 1;
    if (a.severity === "warning") data.summary.open_warning_alerts -= 1;
    return a;
  }
  if (method === "GET" && path === "/v1/work-orders") {
    const [status, vid] = ["status", "vehicle_id"].map((k) => params.get(k));
    return page(data.work_orders.filter((w) => (!status || w.status === status) && (!vid || w.vehicle_id === vid)),
      params);
  }
  if (method === "POST" && (m = path.match(/^\/v1\/work-orders\/([^/]+)\/(schedule|cancel|start|complete)$/))) {
    const [, id, action = ""] = m;
    const w = data.work_orders.find((x) => x.work_order_id === id) ?? fail(404, "Not Found", "No such work order");
    const [from, to] = TRANSITIONS[action] ?? fail(404, "Not Found", `no ${action} action`);
    if (!from.includes(w.status)) fail(409, "Conflict", `cannot ${action} a ${w.status} work order`);
    const b = (body ?? {}) as { scheduled_for?: string; outcome?: string };
    const before = SUMMARY_KEY[w.status];
    const after = SUMMARY_KEY[to];
    if (before) data.summary[before] -= 1;
    if (after) data.summary[after] += 1;
    w.status = to;
    if (b.scheduled_for) w.scheduled_for = b.scheduled_for;
    if (b.outcome) w.outcome = b.outcome;
    return w;
  }
  return fail(404, "Not Found", `${method} ${path} is not available in the demo`);
}

/** Same contract as `fetch` + JSON for the client: resolves with the body or throws a Problem. */
export async function demoRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = new URL(path, "http://demo.invalid");
  const body = typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
  try {
    // A copy, so a page holding the result never sees a later in-place change.
    return structuredClone(route(init.method ?? "GET", url.pathname, url.searchParams, body)) as T;
  } catch (e) {
    if (e instanceof DemoProblem) return Promise.reject(e.problem);
    throw e;
  }
}

/** Replays the most recent recorded alerts into the live feed, one every few seconds. */
export function replayAlerts(onAlert: (a: Record<string, unknown>) => void): () => void {
  const recent = data.alerts.slice(0, 8).reverse();
  let i = 0;
  const timer = setInterval(() => {
    if (i >= recent.length) return clearInterval(timer);
    const a = recent[i++];
    if (a) onAlert({ ...a, fingerprint: `demo-${a.alert_id}`, vin: a.details.vin ?? a.vehicle_id, status: "open" });
  }, 4000);
  return () => clearInterval(timer);
}

// Typed client for the Prognos API (same origin: Vite proxy in dev, nginx in the container).
//
// The access token lives in memory and sessionStorage (cleared when the tab closes; tokens
// expire after 15 minutes). A 401 anywhere signs the user out.

export interface Session {
  token: string;
  userId: string;
  tenantId: string | null;
  roles: string[];
  permissions: string[];
}

export interface Problem {
  status: number;
  title: string;
  detail: string;
  request_id?: string;
}

export class ApiError extends Error {
  constructor(public problem: Problem) {
    super(problem.detail);
  }
}

const KEY = "prognos.session";
let session: Session | null = null;
const listeners = new Set<(s: Session | null) => void>();

function load(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

session = load();

export function currentSession(): Session | null {
  return session;
}

export function onSessionChange(fn: (s: Session | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setSession(next: Session | null): void {
  session = next;
  try {
    if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage unavailable (private mode): keep the in-memory session only
  }
  listeners.forEach((fn) => fn(next));
}

export function signOut(): void {
  setSession(null);
}

export function can(permission: string): boolean {
  return session?.permissions.includes(permission) ?? false;
}

async function toProblem(res: Response): Promise<Problem> {
  try {
    return (await res.json()) as Problem;
  } catch {
    return { status: res.status, title: res.statusText, detail: `HTTP ${res.status}` };
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401 && session) signOut();
  if (!res.ok) throw new ApiError(await toProblem(res));
  return (await res.json()) as T;
}

export async function signIn(email: string, password: string): Promise<Session> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch("/v1/auth/token", { method: "POST", body });
  if (!res.ok) throw new ApiError(await toProblem(res));
  const { access_token } = (await res.json()) as { access_token: string };
  const me = await fetch("/v1/auth/me", { headers: { Authorization: `Bearer ${access_token}` } });
  if (!me.ok) throw new ApiError(await toProblem(me));
  const info = (await me.json()) as {
    user_id: string;
    tenant_id: string | null;
    roles: string[];
    permissions: string[];
  };
  const next: Session = {
    token: access_token,
    userId: info.user_id,
    tenantId: info.tenant_id,
    roles: info.roles,
    permissions: info.permissions,
  };
  setSession(next);
  return next;
}

// --------------------------------------------------------------------------- types
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Summary {
  vehicles: number;
  vehicles_in_workshop: number;
  open_critical_alerts: number;
  open_warning_alerts: number;
  acknowledged_alerts: number;
  work_orders_proposed: number;
  work_orders_scheduled: number;
  work_orders_in_progress: number;
}

export interface LiveState {
  health_score: number | null;
  as_of: string | null;
  latitude: number | null;
  longitude: number | null;
  speed_kmh: number | null;
  ignition_on: boolean | null;
  active_alerts: string[];
  indicators: Record<string, number>;
  location_precision: "precise" | "approx_1km";
}

export interface ModelRisk {
  probability: number;
  model_version: string;
  scored_at: string;
  top_features: { feature: string; contribution: number }[];
}

export interface Vehicle {
  vehicle_id: string;
  vin: string;
  model_code: string;
  model_name: string;
  oem: string;
  powertrain: string;
  model_year: number;
  firmware_version: string;
  status: string;
  open_alerts?: number;
  score?: number;
  live?: LiveState;
  risk?: ModelRisk;
}

export interface AtRisk {
  source: string;
  requested: string;
  fallback: boolean;
  items: Vehicle[];
}

export interface Alert {
  alert_id: number;
  vehicle_id: string;
  rule_code: string;
  severity: "info" | "warning" | "critical";
  failure_mode: string | null;
  status: string;
  event_ts: string;
  detected_at: string;
  acknowledged_at: string | null;
  details: Record<string, unknown>;
}

export interface WorkOrder {
  work_order_id: string;
  vehicle_id: string;
  workshop_id: string;
  failure_mode: string;
  status: "proposed" | "scheduled" | "in_progress" | "completed" | "cancelled";
  failure_probability: number | null;
  expected_cost_avoided: number | null;
  model_version: string | null;
  scheduled_for: string | null;
  created_at: string;
  outcome: string | null;
}

export interface Signal {
  window_start: string;
  window_end: string;
  level: "firmware" | "model";
  dtc: string;
  oem: string;
  model_code: string;
  firmware_version: string | null;
  rate: number;
  baseline_rate: number;
  rate_ratio: number;
  p_adjusted: number;
}

// --------------------------------------------------------------------------- calls
const qs = (params: Record<string, string | number | undefined | null>): string => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && p.set(k, String(v)));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  summary: () => request<Summary>("/v1/fleet/summary"),
  atRisk: (source: "rules" | "model", limit = 25) =>
    request<AtRisk>(`/v1/vehicles/at-risk${qs({ source, limit })}`),
  vehicle: (id: string) => request<Vehicle>(`/v1/vehicles/${encodeURIComponent(id)}`),
  alerts: (params: { status?: string; severity?: string; vehicle_id?: string; cursor?: string | null }) =>
    request<Page<Alert>>(`/v1/alerts${qs({ ...params, limit: 50 })}`),
  acknowledge: (id: number) => request<Alert>(`/v1/alerts/${id}/acknowledge`, { method: "POST" }),
  workOrders: (params: { status?: string; vehicle_id?: string; cursor?: string | null }) =>
    request<Page<WorkOrder>>(`/v1/work-orders${qs({ ...params, limit: 50 })}`),
  transition: (id: string, action: "schedule" | "cancel" | "start" | "complete", body?: object) =>
    request<WorkOrder>(`/v1/work-orders/${id}/${action}`, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  signals: () => request<{ items: Signal[] }>("/v1/fleet/signals"),
};

/** Live alert feed. Returns a function that closes the socket. */
export function subscribeAlerts(onAlert: (a: Record<string, unknown>) => void): () => void {
  if (!session) return () => undefined;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/v1/ws/alerts`);
  const token = session.token;
  ws.onopen = () => ws.send(JSON.stringify({ token }));
  ws.onmessage = (ev) => {
    const msg = JSON.parse(String(ev.data)) as Record<string, unknown>;
    if (msg.type !== "subscribed") onAlert(msg);
  };
  return () => ws.close();
}

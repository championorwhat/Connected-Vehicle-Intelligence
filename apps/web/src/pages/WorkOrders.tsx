import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, can, type WorkOrder } from "../api/client";
import { PageIntro } from "../components/PageIntro";
import { ago, failureLabel, humanize, pct } from "../format";
import { useAsync } from "../useAsync";

const STATUSES = ["proposed", "scheduled", "in_progress", "completed", "cancelled"] as const;

const STATUS_HELP: Record<(typeof STATUSES)[number], string> = {
  proposed: "Suggested by Prognos, waiting for you to book or cancel.",
  scheduled: "Booked into a workshop on the date shown.",
  in_progress: "The vehicle is in the workshop now.",
  completed: "Repair done, with the technician's finding.",
  cancelled: "Not needed.",
};

/** Buttons offered for a work order; the API enforces the same state machine and permissions. */
export function actionsFor(wo: WorkOrder): ("schedule" | "cancel" | "start" | "complete")[] {
  const out: ("schedule" | "cancel" | "start" | "complete")[] = [];
  if (can("work_order:write") && (wo.status === "proposed" || wo.status === "scheduled")) out.push("schedule", "cancel");
  if (can("work_order:complete") && wo.status === "scheduled") out.push("start");
  if (can("work_order:complete") && (wo.status === "scheduled" || wo.status === "in_progress")) out.push("complete");
  return out;
}

/** Where a work order's risk figure came from, in words. */
export function basisLabel(version: string | null): string {
  if (!version) return "Created by hand";
  if (version.startsWith("rules")) return "Alert rules";
  if (version.startsWith("failure-7d")) return "Prediction model";
  return version;
}

export function WorkOrders() {
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("proposed");
  const [message, setMessage] = useState<string | null>(null);
  const page = useAsync(() => api.workOrders({ status }), [status]);

  async function act(wo: WorkOrder, action: "schedule" | "cancel" | "start" | "complete") {
    const name = wo.vin ?? wo.vehicle_id.slice(0, 8);
    let body: object | undefined;
    if (action === "schedule") {
      const date = window.prompt(`Book ${name} into ${wo.workshop_name ?? "the workshop"} on which date? (YYYY-MM-DD)`,
                                 wo.scheduled_for ?? tomorrow());
      if (!date) return;
      body = { scheduled_for: date };
    }
    if (action === "cancel" && !window.confirm(`Cancel the ${failureLabel(wo.failure_mode).toLowerCase()} booking for ${name}?`))
      return;
    if (action === "complete") {
      const confirmed = window.confirm("Did the workshop find the predicted fault?\n\nOK = yes, fault confirmed\nCancel = no fault found");
      body = { outcome: confirmed ? "fault_confirmed" : "no_fault_found" };
    }
    try {
      await api.transition(wo.work_order_id, action, body);
      setMessage(`${name}: ${DONE[action]}`);
      page.reload();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.problem.detail : String(e));
    }
  }

  return (
    <>
      <h1>Work orders</h1>
      <PageIntro lead="Workshop bookings that Prognos proposes before a vehicle is expected to break down. You decide which to book.">
        <ul>
          <li><strong>Proposed</strong>: Prognos picked the nearest workshop with free capacity before the predicted failure. Click <strong>Schedule</strong> to book it, or <strong>Cancel</strong> if it is not needed.</li>
          <li><strong>Chance of failure</strong>: how likely this fault is to cause a breakdown within 7 days.</li>
          <li><strong>Cost avoided</strong> says "not sourced" until real repair costs are entered: Prognos never invents money figures.</li>
          <li>Technicians see <strong>Start</strong> and <strong>Complete</strong> on scheduled jobs, and record what they found.</li>
        </ul>
      </PageIntro>
      <section className="card">
        <div className="filters" role="group" aria-label="Status">
          {STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)} title={STATUS_HELP[s]}>
              {humanize(s)}
            </button>
          ))}
        </div>
        <p className="muted small">{STATUS_HELP[status]}</p>
        {message && <p role="status" className="note">{message}</p>}
        {page.error && <p role="alert" className="error">{page.error}</p>}
        <table>
          <thead>
            <tr>
              <th scope="col">Vehicle</th>
              <th scope="col">Problem</th>
              <th scope="col">Chance of failure (7 days)</th>
              <th scope="col">Workshop</th>
              <th scope="col">{status === "proposed" ? "Suggested date" : "Date"}</th>
              <th scope="col">Cost avoided</th>
              <th scope="col">Based on</th>
              <th scope="col">Created</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {page.data?.items.map((wo) => (
              <tr key={wo.work_order_id}>
                <td>
                  <Link to={`/vehicles/${wo.vehicle_id}`}>{wo.vin ?? wo.vehicle_id.slice(0, 8)}</Link>
                  {wo.model_name && <span className="code">{wo.model_name}</span>}
                </td>
                <td>{failureLabel(wo.failure_mode)}</td>
                <td>{pct(wo.failure_probability)}</td>
                <td>
                  {wo.workshop_name ?? "–"}
                  {wo.workshop_city && <span className="code">{wo.workshop_city}</span>}
                </td>
                <td>{wo.scheduled_for ?? "–"}</td>
                <td title="Shown only when repair costs are sourced">
                  {wo.expected_cost_avoided === null ? "not sourced" : wo.expected_cost_avoided.toLocaleString()}
                </td>
                <td title={wo.model_version ?? "manual"}>{basisLabel(wo.model_version)}</td>
                <td>{ago(wo.created_at)}</td>
                <td className="actions">
                  {actionsFor(wo).map((a) => (
                    <button key={a} type="button" onClick={() => act(wo, a)}>{humanize(a)}</button>
                  ))}
                </td>
              </tr>
            ))}
            {page.data && page.data.items.length === 0 && (
              <tr><td colSpan={9} className="muted">No {humanize(status).toLowerCase()} work orders.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}

const DONE = {
  schedule: "booked",
  cancel: "cancelled",
  start: "repair started",
  complete: "repair completed",
} as const;

function tomorrow(): string {
  const d = new Date(Date.now() + 86_400_000);
  return d.toISOString().slice(0, 10);
}

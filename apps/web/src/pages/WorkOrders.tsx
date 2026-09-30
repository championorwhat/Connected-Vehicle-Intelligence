import { useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, can, type WorkOrder } from "../api/client";
import { ago, humanize, pct } from "../format";
import { useAsync } from "../useAsync";

const STATUSES = ["proposed", "scheduled", "in_progress", "completed", "cancelled"] as const;

/** Buttons offered for a work order; the API enforces the same state machine and permissions. */
export function actionsFor(wo: WorkOrder): ("schedule" | "cancel" | "start" | "complete")[] {
  const out: ("schedule" | "cancel" | "start" | "complete")[] = [];
  if (can("work_order:write") && (wo.status === "proposed" || wo.status === "scheduled")) out.push("schedule", "cancel");
  if (can("work_order:complete") && wo.status === "scheduled") out.push("start");
  if (can("work_order:complete") && (wo.status === "scheduled" || wo.status === "in_progress")) out.push("complete");
  return out;
}

export function WorkOrders() {
  const [status, setStatus] = useState<string>("proposed");
  const [message, setMessage] = useState<string | null>(null);
  const page = useAsync(() => api.workOrders({ status }), [status]);

  async function act(wo: WorkOrder, action: "schedule" | "cancel" | "start" | "complete") {
    let body: object | undefined;
    if (action === "schedule") {
      const date = window.prompt("Schedule for (YYYY-MM-DD)", wo.scheduled_for ?? tomorrow());
      if (!date) return;
      body = { scheduled_for: date };
    }
    if (action === "complete") {
      const confirmed = window.confirm("Was the predicted fault confirmed? OK = confirmed, Cancel = no fault found");
      body = { outcome: confirmed ? "fault_confirmed" : "no_fault_found" };
    }
    try {
      await api.transition(wo.work_order_id, action, body);
      setMessage(`Work order ${wo.work_order_id.slice(0, 8)}: ${action} done`);
      page.reload();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.problem.detail : String(e));
    }
  }

  return (
    <>
      <h1>Work orders</h1>
      <section className="card">
        <div className="filters" role="group" aria-label="Status">
          {STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>
              {humanize(s)}
            </button>
          ))}
        </div>
        {message && <p role="status" className="note">{message}</p>}
        {page.error && <p role="alert" className="error">{page.error}</p>}
        <table>
          <thead>
            <tr>
              <th scope="col">Vehicle</th>
              <th scope="col">Failure mode</th>
              <th scope="col">P(fail)</th>
              <th scope="col">Cost avoided</th>
              <th scope="col">Scheduled</th>
              <th scope="col">Risk source</th>
              <th scope="col">Created</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {page.data?.items.map((wo) => (
              <tr key={wo.work_order_id}>
                <td><Link to={`/vehicles/${wo.vehicle_id}`}>{wo.vehicle_id.slice(0, 8)}</Link></td>
                <td>{humanize(wo.failure_mode)}</td>
                <td>{pct(wo.failure_probability)}</td>
                <td title="Shown only when repair costs are sourced">
                  {wo.expected_cost_avoided === null ? "not sourced" : wo.expected_cost_avoided.toLocaleString()}
                </td>
                <td>{wo.scheduled_for ?? "–"}</td>
                <td><code>{wo.model_version ?? "manual"}</code></td>
                <td>{ago(wo.created_at)}</td>
                <td className="actions">
                  {actionsFor(wo).map((a) => (
                    <button key={a} type="button" onClick={() => act(wo, a)}>{humanize(a)}</button>
                  ))}
                </td>
              </tr>
            ))}
            {page.data && page.data.items.length === 0 && (
              <tr><td colSpan={8} className="muted">No {humanize(status).toLowerCase()} work orders.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}

function tomorrow(): string {
  const d = new Date(Date.now() + 86_400_000);
  return d.toISOString().slice(0, 10);
}

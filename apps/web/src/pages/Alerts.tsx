import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, can, subscribeAlerts, type Alert } from "../api/client";
import { ago, humanize } from "../format";
import { useAsync } from "../useAsync";

export function Alerts() {
  const [status, setStatus] = useState("open");
  const [severity, setSeverity] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [live, setLive] = useState<Record<string, unknown>[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const page = useAsync(() => api.alerts({ status, severity, cursor }), [status, severity, cursor]);

  useEffect(() => subscribeAlerts((a) => setLive((prev) => [a, ...prev].slice(0, 20))), []);

  async function acknowledge(a: Alert) {
    try {
      await api.acknowledge(a.alert_id);
      setMessage(`Alert ${a.alert_id} acknowledged`);
      page.reload();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.problem.detail : String(e));
    }
  }

  return (
    <>
      <h1>Alerts</h1>
      <section className="card" aria-labelledby="live-title">
        <h2 id="live-title">Live feed</h2>
        <ul className="feed" aria-live="polite">
          {live.length === 0 && <li className="muted">Waiting for new alert transitions…</li>}
          {live.map((a, i) => (
            <li key={`${String(a.fingerprint)}-${i}`} className={String(a.severity)}>
              <strong>{String(a.rule_code)}</strong> {String(a.status)} · {String(a.vin ?? a.vehicle_id)}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <div className="filters">
          <label>
            Status
            <select value={status} onChange={(e) => { setStatus(e.target.value); setCursor(null); }}>
              <option value="open">Open</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="resolved">Resolved</option>
              <option value="">All</option>
            </select>
          </label>
          <label>
            Severity
            <select value={severity} onChange={(e) => { setSeverity(e.target.value); setCursor(null); }}>
              <option value="">All</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
            </select>
          </label>
        </div>
        {message && <p role="status" className="note">{message}</p>}
        {page.error && <p role="alert" className="error">{page.error}</p>}
        <table>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Rule</th>
              <th scope="col">Failure mode</th>
              <th scope="col">Vehicle</th>
              <th scope="col">Detected</th>
              <th scope="col">Status</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {page.data?.items.map((a) => (
              <tr key={a.alert_id}>
                <td><span className={`badge ${a.severity === "critical" ? "high" : "medium"}`}>{a.severity}</span></td>
                <td>{a.rule_code}</td>
                <td>{humanize(a.failure_mode)}</td>
                <td><Link to={`/vehicles/${a.vehicle_id}`}>{String(a.details.vin ?? a.vehicle_id.slice(0, 8))}</Link></td>
                <td>{ago(a.detected_at)}</td>
                <td>{a.status}</td>
                <td>
                  {a.status === "open" && can("alert:ack") && (
                    <button type="button" onClick={() => acknowledge(a)}>Acknowledge</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="pager">
          {cursor && <button type="button" onClick={() => setCursor(null)}>First page</button>}
          {page.data?.next_cursor && (
            <button type="button" onClick={() => setCursor(page.data?.next_cursor ?? null)}>Next page</button>
          )}
        </div>
      </section>
    </>
  );
}

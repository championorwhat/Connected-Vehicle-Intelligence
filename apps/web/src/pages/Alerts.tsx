import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, can, subscribeAlerts, type Alert } from "../api/client";
import { PageIntro, SeverityBadge } from "../components/PageIntro";
import { ago, alertLabel, failureLabel, humanize } from "../format";
import { useAsync } from "../useAsync";

const STATUS_WORDS: Record<string, string> = {
  open: "New: nobody has looked at it yet",
  acknowledged: "Seen by someone",
  resolved: "Cleared: the reading is back to normal",
};

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
      setMessage(`Marked as seen: ${alertLabel(a.rule_code)} on ${String(a.details.vin ?? "the vehicle")}`);
      page.reload();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.problem.detail : String(e));
    }
  }

  return (
    <>
      <h1>Alerts</h1>
      <PageIntro lead="Problems Prognos has detected on your vehicles. Critical alerts arrive within seconds of the reading that caused them.">
        <ul>
          <li><strong>Critical</strong> (red): act now, the vehicle may break down. <strong>Warning</strong> (amber): an early sign, plan a check.</li>
          <li><strong>Acknowledge</strong> tells your team you have seen it. It is recorded in the audit log.</li>
          <li>Most warnings already have a proposed workshop booking on <strong>Work orders</strong>.</li>
          <li>The <strong>live feed</strong> shows new alerts the moment they happen, without reloading the page.</li>
        </ul>
      </PageIntro>
      <section className="card" aria-labelledby="live-title">
        <h2 id="live-title"><span className="pulse" aria-hidden="true" /> Live feed</h2>
        <ul className="feed" aria-live="polite">
          {live.length === 0 && <li className="muted">Listening… new alerts will appear here as they happen.</li>}
          {live.map((a, i) => (
            <li key={`${String(a.fingerprint)}-${i}`} className={String(a.severity)}>
              <strong>{alertLabel(String(a.rule_code))}</strong>{" "}
              {a.status === "open" ? "raised" : String(a.status)} on {String(a.vin ?? a.vehicle_id)}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <div className="filters">
          <label>
            Show
            <select value={status} onChange={(e) => { setStatus(e.target.value); setCursor(null); }}>
              <option value="open">New (not yet seen)</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="resolved">Resolved</option>
              <option value="">All</option>
            </select>
          </label>
          <label>
            Severity
            <select value={severity} onChange={(e) => { setSeverity(e.target.value); setCursor(null); }}>
              <option value="">All</option>
              <option value="critical">Critical only</option>
              <option value="warning">Warnings only</option>
            </select>
          </label>
        </div>
        {message && <p role="status" className="note">{message}</p>}
        {page.error && <p role="alert" className="error">{page.error}</p>}
        <table>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">What happened</th>
              <th scope="col">Likely problem</th>
              <th scope="col">Vehicle</th>
              <th scope="col">Detected</th>
              <th scope="col">Status</th>
              <th scope="col"><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {page.data?.items.map((a) => (
              <tr key={a.alert_id}>
                <td><SeverityBadge severity={a.severity} /></td>
                <td>
                  {alertLabel(a.rule_code)}
                  <span className="code">{a.rule_code}</span>
                </td>
                <td>{a.failure_mode ? failureLabel(a.failure_mode) : <span className="muted">–</span>}</td>
                <td><Link to={`/vehicles/${a.vehicle_id}`}>{String(a.details.vin ?? a.vehicle_id.slice(0, 8))}</Link></td>
                <td>{ago(a.detected_at)}</td>
                <td title={STATUS_WORDS[a.status]}>{humanize(a.status)}</td>
                <td>
                  {a.status === "open" && can("alert:ack") && (
                    <button type="button" onClick={() => acknowledge(a)}>Acknowledge</button>
                  )}
                </td>
              </tr>
            ))}
            {page.data && page.data.items.length === 0 && (
              <tr><td colSpan={7} className="muted">No alerts match these filters. That is good news.</td></tr>
            )}
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

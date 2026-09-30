import { Link, useParams } from "react-router-dom";

import { api, type Alert } from "../api/client";
import { SeverityBadge } from "../components/PageIntro";
import { ago, alertLabel, featureLabel, healthWord, humanize, num, pct } from "../format";
import { useAsync } from "../useAsync";

/** One sentence telling a first-time user what to do about this vehicle. */
export function advice(alerts: Alert[]): string {
  const open = alerts.filter((a) => a.status === "open" || a.status === "acknowledged");
  if (open.some((a) => a.rule_code === "VEHICLE_BREAKDOWN")) return "This vehicle has broken down: arrange recovery.";
  if (open.some((a) => a.severity === "critical"))
    return "Critical problem: book a workshop visit now (see Work orders), and consider taking it off the road.";
  if (open.length) return "Early warning: plan a workshop check in the coming days. A booking may already be proposed.";
  return "No open problems.";
}

export function VehicleDetail() {
  const { vehicleId = "" } = useParams();
  const vehicle = useAsync(() => api.vehicle(vehicleId), [vehicleId]);
  const alerts = useAsync(() => api.alerts({ vehicle_id: vehicleId, status: "" }), [vehicleId]);
  const v = vehicle.data;

  if (vehicle.error) return <p role="alert" className="error">{vehicle.error}</p>;
  if (!v) return <p className="muted">Loading…</p>;
  const live = v.live;

  return (
    <>
      <p><Link to="/">← Back to overview</Link></p>
      <h1>{v.model_name} <span className="muted">{v.vin}</span></h1>
      {alerts.data && <p className="note advice">{advice(alerts.data.items)}</p>}
      <section className="split">
        <div className="card">
          <h2>Vehicle</h2>
          <dl className="facts">
            <dt>Health score</dt>
            <dd>
              <strong>{num(live?.health_score ?? null)}</strong> / 100{" "}
              <span className="muted">({healthWord(live?.health_score)})</span>
            </dd>
            <dt>Open alerts</dt><dd>{num(v.open_alerts ?? 0)}</dd>
            <dt>Last report</dt><dd>{ago(live?.as_of)}</dd>
            <dt>Make / model</dt><dd>{v.oem} · {v.model_code} ({v.powertrain === "BEV" ? "electric" : v.powertrain === "HEV" ? "hybrid" : "petrol/diesel"})</dd>
            <dt>Model year</dt><dd>{v.model_year}</dd>
            <dt>Software version</dt><dd>{v.firmware_version}</dd>
            <dt>Status</dt><dd>{humanize(v.status)}</dd>
            <dt>Position</dt>
            <dd>
              {live?.latitude != null ? `${live.latitude}, ${live.longitude}` : "–"}
              {live?.location_precision === "approx_1km" && <span className="muted"> (masked to ~1 km for your role)</span>}
            </dd>
          </dl>
        </div>
        <div className="card">
          <h2>Failure risk (next 7 days)</h2>
          {v.risk ? (
            <>
              <p className="big">{pct(v.risk.probability)}</p>
              <p className="muted">
                Chance of a breakdown in the next 7 days, from the prediction model ({v.risk.model_version}, scored{" "}
                {ago(v.risk.scored_at)}). It runs in shadow mode: shown for comparison, not yet used for bookings.
              </p>
              <h3>Why</h3>
              <ul className="reasons">
                {v.risk.top_features.map((f) => (
                  <li key={f.feature}>
                    <span>{featureLabel(f.feature)}</span>
                    <span className={f.contribution > 0 ? "up" : "down"}>
                      {f.contribution > 0 ? "raises" : "lowers"} risk
                    </span>
                  </li>
                ))}
                {v.risk.top_features.length === 0 && <li className="muted">Low risk: no dominant factor.</li>}
              </ul>
            </>
          ) : (
            <p className="muted">
              No model score yet: the model only scores a vehicle once it has about 45 minutes of recent data, so it
              never guesses from too little. The alert rules below already apply.
            </p>
          )}
        </div>
      </section>
      <section className="card">
        <h2>Alert history</h2>
        <table>
          <thead>
            <tr><th scope="col">Severity</th><th scope="col">What happened</th><th scope="col">Status</th><th scope="col">Detected</th></tr>
          </thead>
          <tbody>
            {alerts.data?.items.map((a) => (
              <tr key={a.alert_id}>
                <td><SeverityBadge severity={a.severity} /></td>
                <td>{alertLabel(a.rule_code)}<span className="code">{a.rule_code}</span></td>
                <td>{humanize(a.status)}</td>
                <td>{ago(a.detected_at)}</td>
              </tr>
            ))}
            {alerts.data?.items.length === 0 && <tr><td colSpan={4} className="muted">No alerts for this vehicle.</td></tr>}
          </tbody>
        </table>
      </section>
    </>
  );
}

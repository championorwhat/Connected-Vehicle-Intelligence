import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import { ago, featureLabel, humanize, num, pct } from "../format";
import { useAsync } from "../useAsync";

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
      <p><Link to="/">← Overview</Link></p>
      <h1>{v.model_name} <span className="muted">{v.vin}</span></h1>
      <section className="split">
        <div className="card">
          <h2>Vehicle</h2>
          <dl className="facts">
            <dt>OEM / model</dt><dd>{v.oem} · {v.model_code} ({v.powertrain})</dd>
            <dt>Model year</dt><dd>{v.model_year}</dd>
            <dt>Firmware</dt><dd>{v.firmware_version}</dd>
            <dt>Status</dt><dd>{humanize(v.status)}</dd>
            <dt>Open alerts</dt><dd>{num(v.open_alerts ?? 0)}</dd>
            <dt>Health score</dt><dd>{num(live?.health_score ?? null)}</dd>
            <dt>Last report</dt><dd>{ago(live?.as_of)}</dd>
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
                {v.risk.model_version} · scored {ago(v.risk.scored_at)} · shadow mode (not used for bookings yet)
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
              No model score: the vehicle needs about 45 minutes of recent data. The rules below still apply.
            </p>
          )}
        </div>
      </section>
      <section className="card">
        <h2>Alert history</h2>
        <table>
          <thead>
            <tr><th scope="col">Rule</th><th scope="col">Severity</th><th scope="col">Status</th><th scope="col">Detected</th></tr>
          </thead>
          <tbody>
            {alerts.data?.items.map((a) => (
              <tr key={a.alert_id}>
                <td>{a.rule_code}</td><td>{a.severity}</td><td>{a.status}</td><td>{ago(a.detected_at)}</td>
              </tr>
            ))}
            {alerts.data?.items.length === 0 && <tr><td colSpan={4} className="muted">No alerts.</td></tr>}
          </tbody>
        </table>
      </section>
    </>
  );
}

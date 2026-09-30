import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, can, type AtRisk } from "../api/client";
import { FleetMap, riskBand } from "../components/FleetMap";
import { ago, num, pct } from "../format";
import { useAsync } from "../useAsync";

export function Overview() {
  const [source, setSource] = useState<"rules" | "model">("rules");
  const navigate = useNavigate();
  const summary = useAsync(() => (can("fleet:read") ? api.summary() : Promise.resolve(null)), []);
  const risk = useAsync<AtRisk>(() => api.atRisk(source), [source]);
  const open = useCallback((id: string) => navigate(`/vehicles/${id}`), [navigate]);
  const s = summary.data;

  return (
    <>
      <h1>Fleet overview</h1>
      {s && (
        <section className="tiles" aria-label="Fleet summary">
          <Tile label="Vehicles" value={num(s.vehicles)} />
          <Tile label="Critical alerts open" value={num(s.open_critical_alerts)} tone="bad" />
          <Tile label="Warnings open" value={num(s.open_warning_alerts)} tone="warn" />
          <Tile label="Work orders proposed" value={num(s.work_orders_proposed)} />
          <Tile label="Scheduled" value={num(s.work_orders_scheduled)} />
          <Tile label="In workshop" value={num(s.work_orders_in_progress)} />
        </section>
      )}

      <section className="split">
        <div className="card">
          <div className="card-head">
            <h2>Most at risk</h2>
            <div className="toggle" role="group" aria-label="Ranking source">
              <button type="button" aria-pressed={source === "rules"} onClick={() => setSource("rules")}>
                Rules
              </button>
              <button type="button" aria-pressed={source === "model"} onClick={() => setSource("model")}>
                Model (shadow)
              </button>
            </div>
          </div>
          {risk.data && <SourceNote data={risk.data} />}
          {risk.error && <p role="alert" className="error">{risk.error}</p>}
          <table>
            <thead>
              <tr>
                <th scope="col">Vehicle</th>
                <th scope="col">Model</th>
                <th scope="col">{risk.data?.source.startsWith("model") ? "P(breakdown 7 d)" : "Health"}</th>
                <th scope="col">Alerts</th>
                <th scope="col">Seen</th>
              </tr>
            </thead>
            <tbody>
              {risk.data?.items.map((v) => (
                <tr key={v.vehicle_id} className="clickable" onClick={() => open(v.vehicle_id)}>
                  <td>
                    <a href={`/vehicles/${v.vehicle_id}`} onClick={(e) => { e.preventDefault(); open(v.vehicle_id); }}>
                      {v.vin}
                    </a>
                  </td>
                  <td>{v.model_name}</td>
                  <td>
                    <span className={`badge ${riskBand(v)}`}>
                      {v.risk ? pct(v.risk.probability) : num(v.live?.health_score ?? null)}
                    </span>
                  </td>
                  <td>{v.live?.active_alerts.join(", ") || "–"}</td>
                  <td>{ago(v.live?.as_of)}</td>
                </tr>
              ))}
              {risk.data && risk.data.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">No vehicles ranked yet (the pipeline has not reported).</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Where they are</h2>
          <FleetMap vehicles={risk.data?.items ?? []} onSelect={open} />
        </div>
      </section>
    </>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: "bad" | "warn" }) {
  return (
    <div className={`tile ${tone ?? ""}`}>
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
    </div>
  );
}

export function SourceNote({ data }: { data: AtRisk }) {
  if (data.fallback)
    return (
      <p className="note" role="status">
        No live model scores yet (a vehicle needs 45 minutes of data): showing the rules ranking.
      </p>
    );
  if (data.source.startsWith("model"))
    return (
      <p className="note" role="status">
        Model ranking ({data.source.replace("model:", "")}) in <strong>shadow mode</strong>: shown for comparison,
        not used to book work orders.
      </p>
    );
  return null;
}

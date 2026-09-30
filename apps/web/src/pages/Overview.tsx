import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, can, type AtRisk } from "../api/client";
import { FleetMap, riskBand } from "../components/FleetMap";
import { PageIntro } from "../components/PageIntro";
import { ago, alertShort, healthWord, num, pct } from "../format";
import { useAsync } from "../useAsync";

export function Overview() {
  const [source, setSource] = useState<"rules" | "model">("rules");
  const navigate = useNavigate();
  const summary = useAsync(() => (can("fleet:read") ? api.summary() : Promise.resolve(null)), []);
  const risk = useAsync<AtRisk>(() => api.atRisk(source), [source]);
  const open = useCallback((id: string) => navigate(`/vehicles/${id}`), [navigate]);
  const s = summary.data;
  const byModel = risk.data?.source.startsWith("model") ?? false;

  return (
    <>
      <h1>Fleet overview</h1>
      <PageIntro lead="Prognos watches every vehicle's live data and tells you which ones need a workshop visit before they break down.">
        <ol>
          <li>The numbers below summarise the fleet right now.</li>
          <li><strong>Most at risk</strong> lists the vehicles that need attention first. Click one to see what is wrong.</li>
          <li>Red means act now, amber means keep an eye on it. The map shows where those vehicles are.</li>
          <li>New problems appear on <strong>Alerts</strong>; proposed workshop bookings on <strong>Work orders</strong>.</li>
        </ol>
      </PageIntro>
      {s && (
        <section className="tiles" aria-label="Fleet summary">
          <Tile label="Vehicles" value={num(s.vehicles)} hint="in your fleet" />
          <Tile label="Critical alerts open" value={num(s.open_critical_alerts)} tone="bad" hint="need action now" />
          <Tile label="Warnings open" value={num(s.open_warning_alerts)} tone="warn" hint="early signs of a fault" />
          <Tile label="Work orders proposed" value={num(s.work_orders_proposed)} hint="bookings waiting for you" />
          <Tile label="Scheduled" value={num(s.work_orders_scheduled)} hint="booked into a workshop" />
          <Tile label="In workshop" value={num(s.work_orders_in_progress)} hint="being repaired" />
        </section>
      )}

      <section className="split">
        <div className="card">
          <div className="card-head">
            <h2>Most at risk</h2>
            <div className="toggle" role="group" aria-label="Ranking source">
              <button type="button" aria-pressed={source === "rules"} onClick={() => setSource("rules")}
                      title="Ranked by the alerts each vehicle has raised (what the system acts on)">
                Rules
              </button>
              <button type="button" aria-pressed={source === "model"} onClick={() => setSource("model")}
                      title="Ranked by the machine-learning model (shown for comparison only)">
                Model (shadow)
              </button>
            </div>
          </div>
          <p className="muted small">
            {byModel
              ? "Chance of a breakdown in the next 7 days, according to the model."
              : "Health score from 0 to 100: 100 means no problems, lower means more urgent."}{" "}
            Click a vehicle for details.
          </p>
          {risk.data && <SourceNote data={risk.data} />}
          {risk.error && <p role="alert" className="error">{risk.error}</p>}
          <table>
            <thead>
              <tr>
                <th scope="col">Vehicle</th>
                <th scope="col">Model</th>
                <th scope="col">{byModel ? "Breakdown risk (7 days)" : "Health"}</th>
                <th scope="col">What is wrong</th>
                <th scope="col">Last report</th>
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
                    {!v.risk && <span className="band-word">{healthWord(v.live?.health_score)}</span>}
                  </td>
                  <td>
                    <ul className="chips">
                      {(v.live?.active_alerts ?? []).map((code) => (
                        <li key={code} title={code}>{alertShort(code)}</li>
                      ))}
                      {!v.live?.active_alerts.length && <li className="muted plain">No open alerts</li>}
                    </ul>
                  </td>
                  <td>{ago(v.live?.as_of)}</td>
                </tr>
              ))}
              {risk.data && risk.data.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    No vehicles ranked yet. The pipeline has not reported: start it with <code>make pipeline-demo</code>.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Where they are</h2>
          <p className="muted small">The vehicles in the list. Red: needs attention; amber: keep an eye on. Click a dot to open it.</p>
          <FleetMap vehicles={risk.data?.items ?? []} onSelect={open} />
        </div>
      </section>
    </>
  );
}

function Tile({ label, value, hint, tone }: { label: string; value: string; hint: string; tone?: "bad" | "warn" }) {
  return (
    <div className={`tile ${tone ?? ""}`}>
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
      <span className="tile-hint">{hint}</span>
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

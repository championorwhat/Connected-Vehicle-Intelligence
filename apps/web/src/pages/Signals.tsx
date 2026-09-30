import { api } from "../api/client";
import { humanize, pct } from "../format";
import { useAsync } from "../useAsync";

export function Signals() {
  const signals = useAsync(() => api.signals(), []);
  return (
    <>
      <h1>Emerging fault signals</h1>
      <p className="muted">
        A fault code that is much more common in one firmware release or model than in comparable vehicles
        (exact test, corrected for multiple comparisons). Rates only: counts from other fleets are not shown.
      </p>
      <section className="card">
        {signals.error && <p role="alert" className="error">{signals.error}</p>}
        <table>
          <thead>
            <tr>
              <th scope="col">Fault code</th>
              <th scope="col">Cohort</th>
              <th scope="col">Rate</th>
              <th scope="col">Baseline</th>
              <th scope="col">× baseline</th>
              <th scope="col">Window end</th>
            </tr>
          </thead>
          <tbody>
            {signals.data?.items.map((s) => (
              <tr key={`${s.model_code}-${s.firmware_version ?? "*"}-${s.dtc}-${s.window_end}`}>
                <td>{s.dtc}</td>
                <td>
                  {s.oem} {s.model_code} {s.firmware_version ? `firmware ${s.firmware_version}` : "(all firmware)"}
                  <span className="muted"> · {humanize(s.level)} level</span>
                </td>
                <td>{pct(s.rate, 1)}</td>
                <td>{pct(s.baseline_rate, 1)}</td>
                <td><span className="badge high">{s.rate_ratio.toFixed(1)}×</span></td>
                <td>{new Date(s.window_end).toLocaleString()}</td>
              </tr>
            ))}
            {signals.data?.items.length === 0 && (
              <tr><td colSpan={6} className="muted">No emerging faults in the last 72 hours.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}

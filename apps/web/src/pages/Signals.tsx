import { api } from "../api/client";
import { PageIntro } from "../components/PageIntro";
import { dtcLabel, humanize, pct } from "../format";
import { useAsync } from "../useAsync";

export function Signals() {
  const signals = useAsync(() => api.signals(), []);
  return (
    <>
      <h1>Emerging fault signals</h1>
      <PageIntro lead="Faults that are spreading across one vehicle model or software version, which no single vehicle's alert can reveal: an early sign of a bad software release or parts batch.">
        <ul>
          <li><strong>Rate</strong>: share of those vehicles reporting the fault. <strong>Baseline</strong>: the share among comparable vehicles.</li>
          <li><strong>× baseline</strong>: how many times more common it is. A statistical test only shows a signal when it is very unlikely to be chance.</li>
          <li>Rates only: vehicle counts from other companies' fleets are never shown.</li>
        </ul>
      </PageIntro>
      <section className="card">
        {signals.error && <p role="alert" className="error">{signals.error}</p>}
        <table>
          <thead>
            <tr>
              <th scope="col">Fault</th>
              <th scope="col">Which vehicles</th>
              <th scope="col">Rate</th>
              <th scope="col">Baseline</th>
              <th scope="col">× baseline</th>
              <th scope="col">Seen in the window ending</th>
            </tr>
          </thead>
          <tbody>
            {signals.data?.items.map((s) => (
              <tr key={`${s.model_code}-${s.firmware_version ?? "*"}-${s.dtc}-${s.window_end}`}>
                <td>{dtcLabel(s.dtc)}<span className="code">{s.dtc}</span></td>
                <td>
                  {s.oem} {s.model_code} {s.firmware_version ? `on software ${s.firmware_version}` : "(all software versions)"}
                  <span className="muted"> · {humanize(s.level)} level</span>
                </td>
                <td>{pct(s.rate, 1)}</td>
                <td>{pct(s.baseline_rate, 1)}</td>
                <td><span className="badge high">{s.rate_ratio.toFixed(1)}×</span></td>
                <td>{new Date(s.window_end).toLocaleString()}</td>
              </tr>
            ))}
            {signals.data?.items.length === 0 && (
              <tr><td colSpan={6} className="muted">No spreading faults in the last 72 hours.</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}

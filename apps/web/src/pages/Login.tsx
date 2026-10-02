import { useState, type FormEvent } from "react";

import { ApiError, DEMO, enterDemo, signIn } from "../api/client";
import { DemoBanner } from "../components/DemoBanner";

const DEMO_ROLES = [
  ["fleet_manager", "Fleet manager", "sees everything, books repairs"],
  ["technician", "Technician", "starts and completes repairs"],
  ["analyst", "Analyst", "read-only, locations blurred to ~1 km"],
  ["dpo", "Data protection officer", "handles data-erasure requests"],
] as const;

/** Demo shortcuts only on a local machine, never on a deployed site. */
const LOCAL = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.problem.detail : "Cannot reach the API");
    } finally {
      setBusy(false);
    }
  }

  if (DEMO) return <DemoLogin />;

  return (
    <main className="login">
      <div className="login-panel">
        <section className="login-about" aria-labelledby="about-title">
          <h2 id="about-title">Know which vehicle fails next</h2>
          <p>
            Prognos reads live data from every vehicle in a fleet, raises an alert within seconds when something is
            critical, and proposes a workshop booking before a vehicle breaks down.
          </p>
          {LOCAL && (<>
          <h3>Demo accounts</h3>
          <p className="muted small">
            Choose one to fill in the email. The password is <code>DEMO_USER_PASSWORD</code> from your{" "}
            <code>.env</code> file.
          </p>
          <ul className="roles">
            {DEMO_ROLES.map(([id, name, what]) => (
              <li key={id}>
                <button type="button" className="link" onClick={() => setEmail(`${id}@demo.prognos.local`)}>
                  {name}
                </button>
                <span className="muted"> · {what}</span>
              </li>
            ))}
          </ul>
          </>)}
        </section>
        <form onSubmit={submit} className="card" aria-labelledby="login-title">
          <h1 id="login-title">Prognos</h1>
          <p className="muted">Predictive maintenance for connected vehicle fleets.</p>
          <label>
            Email
            <input type="email" autoComplete="username" required value={email}
                   onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" autoComplete="current-password" required value={password}
                   onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p role="alert" className="error">{error}</p>}
          <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
    </main>
  );
}

/** GitHub Pages build: there are no accounts, only the recorded fleet-manager view. */
function DemoLogin() {
  return (
    <>
      <DemoBanner />
      <main className="login">
        <div className="login-panel">
          <section className="login-about" aria-labelledby="about-title">
            <h2 id="about-title">Know which vehicle fails next</h2>
            <p>
              Prognos reads live data from every vehicle in a fleet, raises an alert within seconds when something is
              critical, and proposes a workshop booking before a vehicle breaks down.
            </p>
            <p className="muted small">
              This demo shows the dashboard a fleet manager saw during a recorded run of the full system.
            </p>
          </section>
          <div className="card login-demo">
            <h1>Prognos</h1>
            <p className="muted">Predictive maintenance for connected vehicle fleets.</p>
            <button type="button" className="primary" onClick={() => void enterDemo()}>
              Open the demo as a fleet manager
            </button>
          </div>
        </div>
      </main>
    </>
  );
}

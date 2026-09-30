import { NavLink, Outlet } from "react-router-dom";

import { signOut, type Session } from "../api/client";
import { roleLabel } from "../format";

const LINKS: { to: string; label: string; hint: string; permission?: string }[] = [
  { to: "/", label: "Overview", hint: "The whole fleet at a glance and the vehicles most at risk" },
  { to: "/alerts", label: "Alerts", hint: "Problems detected on vehicles, as they happen", permission: "alert:read" },
  { to: "/work-orders", label: "Work orders", hint: "Workshop bookings proposed before a breakdown",
    permission: "work_order:read" },
  { to: "/signals", label: "Fleet signals", hint: "Faults spreading across one vehicle model or software version",
    permission: "fleet:read" },
];

export function Layout({ session }: { session: Session }) {
  const visible = LINKS.filter((l) => !l.permission || session.permissions.includes(l.permission));
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">
          Prognos <span className="tagline">predictive maintenance</span>
        </span>
        <nav aria-label="Main">
          {visible.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.to === "/"} title={l.hint}>
              {l.label}
            </NavLink>
          ))}
        </nav>
        <span className="who">
          <span>Signed in as {session.roles.map(roleLabel).join(", ")}</span>
          <button type="button" className="link" onClick={signOut}>
            Sign out
          </button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="footer">
        Simulated fleet data · the prediction model runs in shadow mode · money is shown only when repair costs
        have a source
      </footer>
    </div>
  );
}

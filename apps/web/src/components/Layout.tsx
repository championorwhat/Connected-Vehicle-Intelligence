import { NavLink, Outlet } from "react-router-dom";

import { signOut, type Session } from "../api/client";

const LINKS: { to: string; label: string; permission?: string }[] = [
  { to: "/", label: "Overview" },
  { to: "/alerts", label: "Alerts", permission: "alert:read" },
  { to: "/work-orders", label: "Work orders", permission: "work_order:read" },
  { to: "/signals", label: "Fleet signals", permission: "fleet:read" },
];

export function Layout({ session }: { session: Session }) {
  const visible = LINKS.filter((l) => !l.permission || session.permissions.includes(l.permission));
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Prognos</span>
        <nav aria-label="Main">
          {visible.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.to === "/"}>
              {l.label}
            </NavLink>
          ))}
        </nav>
        <span className="who">
          {session.roles.map((r) => r.replace("_", " ")).join(", ")}
          <button type="button" className="link" onClick={signOut}>
            Sign out
          </button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="footer">
        Simulated fleet data · model runs in shadow mode · money shown only when costs are sourced
      </footer>
    </div>
  );
}

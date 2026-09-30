import "leaflet/dist/leaflet.css";
import "./styles.css";

import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { currentSession, onSessionChange, type Session } from "./api/client";
import { Layout } from "./components/Layout";
import { Alerts } from "./pages/Alerts";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Signals } from "./pages/Signals";
import { VehicleDetail } from "./pages/VehicleDetail";
import { WorkOrders } from "./pages/WorkOrders";

export function App() {
  const [session, setSession] = useState<Session | null>(currentSession());
  useEffect(() => onSessionChange(setSession), []);

  if (!session) return <Login />;
  return (
    <Routes>
      <Route element={<Layout session={session} />}>
        <Route index element={<Overview />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="work-orders" element={<WorkOrders />} />
        <Route path="vehicles/:vehicleId" element={<VehicleDetail />} />
        <Route path="signals" element={<Signals />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  );
}

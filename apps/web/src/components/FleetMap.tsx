import L from "leaflet";
import { useEffect, useRef } from "react";

import type { Vehicle } from "../api/client";

/** At-risk vehicles on an OpenStreetMap base layer; colour = risk band. */
export function FleetMap({ vehicles, onSelect }: { vehicles: Vehicle[]; onSelect: (id: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!host.current || map.current) return;
    map.current = L.map(host.current, { zoomControl: true }).setView([21, 79], 4); // India
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors",
      // The page sends no referrer at all (security headers), but OpenStreetMap's tile
      // usage policy blocks tile requests without one ("Access blocked"). Tiles alone
      // send the site's origin, never the page path.
      referrerPolicy: "strict-origin-when-cross-origin",
    }).addTo(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const group = layer.current;
    if (!group) return;
    group.clearLayers();
    const points: L.LatLngTuple[] = [];
    for (const v of vehicles) {
      const lat = v.live?.latitude;
      const lon = v.live?.longitude;
      if (lat == null || lon == null) continue;
      points.push([lat, lon]);
      const band = riskBand(v);
      L.circleMarker([lat, lon], { radius: 7, weight: 2, className: `marker ${band}` })
        .bindTooltip(`${v.model_name} · ${v.vin}`)
        .on("click", () => onSelect(v.vehicle_id))
        .addTo(group);
    }
    if (points.length && map.current) map.current.fitBounds(points, { padding: [30, 30], maxZoom: 9 });
  }, [vehicles, onSelect]);

  return <div ref={host} className="map" role="region" aria-label="Map of at-risk vehicles" />;
}

export function riskBand(v: Vehicle): "high" | "medium" | "low" {
  const p = v.risk?.probability;
  if (p !== undefined) return p >= 0.5 ? "high" : p >= 0.2 ? "medium" : "low";
  const h = v.live?.health_score ?? 100;
  return h <= 60 ? "high" : h < 100 ? "medium" : "low";
}

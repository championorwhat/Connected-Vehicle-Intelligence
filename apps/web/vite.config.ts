/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server proxies the API so the browser talks to one origin (no CORS), exactly as
// nginx does in the container.
const api = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  // The demo build is served from a sub-path on GitHub Pages (/<repo>/), so assets load relatively.
  base: process.env.VITE_DEMO === "1" ? "./" : "/",
  server: {
    port: 5173,
    proxy: {
      "/v1/ws": { target: api.replace(/^http/, "ws"), ws: true },
      "/v1": api,
    },
  },
  build: { sourcemap: true },
  test: { environment: "jsdom", include: ["tests/unit/**/*.test.tsx", "tests/unit/**/*.test.ts"] },
});

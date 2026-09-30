import { defineConfig } from "@playwright/test";

// End-to-end against a running stack (`make pipeline-demo users`), default: the nginx
// container on :8080. CHROMIUM_PATH lets CI/sandboxes use a preinstalled browser.
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    viewport: { width: 1400, height: 900 },
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
});

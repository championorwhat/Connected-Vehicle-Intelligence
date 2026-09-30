import { expect, test, type Page } from "@playwright/test";

const password = process.env.DEMO_USER_PASSWORD ?? "change-me-local-only";
const shots = process.env.SCREENSHOT_DIR;

async function signIn(page: Page, role: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(`${role}@demo.prognos.local`);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Fleet overview" })).toBeVisible();
}

async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true });
}

test("fleet manager: overview, shadow toggle, alerts, work orders, vehicle", async ({ page }) => {
  await signIn(page, "fleet_manager");
  await expect(page.getByRole("region", { name: "Fleet summary" })).toContainText("Vehicles");
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await shot(page, "overview");

  await page.getByRole("button", { name: "Model (shadow)" }).click();
  await expect(page.getByRole("status").first()).toContainText(/shadow mode|rules ranking/);

  await page.getByRole("link", { name: "Alerts" }).click();
  await expect(page.getByRole("heading", { name: "Alerts" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Acknowledge" }).first()).toBeVisible();
  await shot(page, "alerts");

  await page.getByRole("link", { name: "Work orders" }).click();
  await expect(page.getByRole("button", { name: "Schedule", exact: true }).first()).toBeVisible();
  await expect(page.getByText("not sourced").first()).toBeVisible();
  await shot(page, "work-orders");

  await page.getByRole("link", { name: "Overview" }).click();
  await page.locator("tbody tr a").first().click();
  await expect(page.getByRole("heading", { name: "Failure risk (next 7 days)" })).toBeVisible();
  await shot(page, "vehicle");
});

test("technician sees no acknowledge button and cannot schedule", async ({ page }) => {
  await signIn(page, "technician");
  await page.getByRole("link", { name: "Alerts" }).click();
  await expect(page.getByRole("heading", { name: "Alerts" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Acknowledge" })).toHaveCount(0);
  await page.getByRole("link", { name: "Work orders" }).click();
  await expect(page.getByRole("button", { name: "Schedule", exact: true })).toHaveCount(0);
});

test("analyst sees masked locations", async ({ page }) => {
  await signIn(page, "analyst");
  await page.locator("tbody tr a").first().click();
  await expect(page.getByText("masked to ~1 km for your role")).toBeVisible();
});

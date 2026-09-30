import { defineConfig, devices } from "@playwright/test";

// Runs against the real backend (BACKEND_URL in .env.local, simulation mode) through the
// dashboard's proxy. E2E_PORT defaults to 3100 so it does not collide with `npm run dev`.
const port = process.env.E2E_PORT ?? "3100";
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${port}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `npx next dev --port ${port}`,
        url: `${baseURL}/api/backend/system`,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});

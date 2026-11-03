import { defineConfig, devices } from "@playwright/test";

// e2e against the built frontend (npm run build) served by the backend in fake mode
// (docs/specs/spec_cockpit.md §17). tests/e2e-server.sh recreates the e2e database on every run.
export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false,
  workers: 1, // one database: the tests run in order (first user first)
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:8401",
    viewport: { width: 1280, height: 800 }, // the laptop width of §7.1
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } }],
  webServer: {
    command: "bash tests/e2e-server.sh",
    url: "http://127.0.0.1:8401/api/setup-account",
    timeout: 60_000,
    reuseExistingServer: false,
    stdout: "pipe",
  },
});

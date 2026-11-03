import { defineConfig, devices } from "@playwright/test";
import base from "./playwright.config";

// C3 acceptance against the REAL host (docs/changelog/cockpit-c3.md): the e2e server without fake mode,
// so the cockpit runs `make …` and talks to the real agent. Local only, never in CI; it
// starts and stops ROS stacks on this machine (no hardware). `npm run test:real`.
export default defineConfig({
  ...base,
  testDir: "tests/real",
  timeout: 600_000,
  retries: 0,
  reporter: "list",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } }],
  webServer: { ...base.webServer!, command: "COCKPIT_FAKE=0 bash tests/e2e-server.sh" } as never,
});

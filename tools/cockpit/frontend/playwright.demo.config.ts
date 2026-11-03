import { defineConfig, devices } from "@playwright/test";
import real from "./playwright.real.config";

// The demo recording (tools/demo/README.md): the real e2e server like the real acceptance
// tests, one long serial test, 1280×720 at 1.5× pixel density (recorded as 1920×1080).
// `make demo-video` runs it and renders the videos. DEMO_THEME=dark records the GUI in dark
// mode: the cockpit's theme follows the OS (no stored choice in a fresh browser), and
// Playwright emulates the OS colour scheme.
const THEME = process.env.DEMO_THEME ?? "light";
if (THEME !== "light" && THEME !== "dark") throw new Error(`DEMO_THEME must be light or dark, not '${THEME}'`);

export default defineConfig({
  ...real,
  testDir: "tests/demo",
  timeout: 60 * 60_000,
  retries: 0,
  reporter: "list",
  use: { ...real.use, trace: "off", video: "off" },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5, colorScheme: THEME },
    },
  ],
});

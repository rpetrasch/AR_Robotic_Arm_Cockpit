import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

// Phase C3 acceptance in fake mode (docs/specs/spec_cockpit.md §18): start / stop from the UI with
// the shown command, stop and switch, components, viewers, the header's software stop, and
// the REAL pre-flight wizard with an e-stop transition. Runs after c1 (user) and c2.
const STATE = path.join(__dirname, "..", "..", ".e2e-data", "fake", "state.json");

function fakeState(state: object) {
  mkdirSync(path.dirname(STATE), { recursive: true });
  writeFileSync(STATE, JSON.stringify(state));
}

function patchState(patch: object) {
  fakeState({ ...JSON.parse(readFileSync(STATE, "utf8")), ...patch });
}

async function signIn(page: Page) {
  await page.goto("/login/");
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password").fill("e2e-long-password");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/environment\/$/);
}

async function jobSucceeded(page: Page) {
  await expect(page.getByTestId("env-job-status")).toHaveText("succeeded", { timeout: 30_000 });
}

test.beforeAll(() => fakeState({ images: { "ar3-dev": true, "ar3-dev-vnc": true } }));

test("start from the form: the shown command is the one that runs", async ({ page }) => {
  await signIn(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("STOPPED");
  await page.getByTestId("robot-gazebo").click();
  await page.getByTestId("rviz").click();
  const shown = "AR3_MODE=sim make vnc DETACH=1";
  await expect(page.getByTestId("start-command")).toContainText(shown);
  await page.getByTestId("start").click();
  await expect(page.getByTestId("env-job")).toContainText(shown);
  await jobSucceeded(page);
  await expect(page.getByTestId("env-job")).toContainText("ready.");
  // The log tab is one screen tall: the log scrolls, the page does not (no double scrollbar).
  const overflow = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
  expect(overflow).toBeLessThanOrEqual(1);
  expect((await page.getByTestId("job-log").boundingBox())!.height).toBeGreaterThan(160);
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · GAZEBO", { timeout: 10_000 });
  await expect(page.getByTestId("env-ready")).toBeVisible();
  await expect(page.getByTestId("env-current")).toContainText("AR3_MODE=sim make vnc");
  await expect(page.getByTestId("status-pill")).toHaveText("OK");
});

test("a second stack: stop and switch", async ({ page }) => {
  await signIn(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · GAZEBO", { timeout: 10_000 });
  await page.getByTestId("robot-mock").click(); // RViz in the browser off: make up-mac
  await expect(page.getByTestId("start")).toHaveText(/Stop and switch/);
  await expect(page.getByTestId("start-command")).toContainText("make down && make up-mac DETACH=1");
  await page.getByTestId("start").click();
  await page.getByTestId("confirm-switch").click();
  await jobSucceeded(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · MOCK", { timeout: 10_000 });
  await expect(page.getByTestId("env-current")).toContainText("make up-mac");
});

test("the header's software stop reaches the agent", async ({ page }) => {
  await signIn(page);
  await expect(page.getByTestId("stop-button")).toBeEnabled({ timeout: 10_000 });
  await page.getByTestId("stop-button").click();
  await expect(page.getByText(/Software stop: \d+ goal\(s\) cancelled/)).toBeVisible();
  expect(JSON.parse(readFileSync(STATE, "utf8")).stops).toBe(1);
});

test("components: health, diagram for the mode, CLI per component", async ({ page }) => {
  await signIn(page);
  await page.getByRole("tab", { name: "Components" }).click();
  await expect(page.getByTestId("health-move_group")).toContainText("ready", { timeout: 10_000 });
  await expect(page.getByTestId("health-gazebo")).toContainText("off"); // mock
  await expect(page.getByTestId("node-gazebo")).toHaveAttribute("data-in-mode", "false");
  await expect(page.getByTestId("node-move_group")).toHaveAttribute("data-in-mode", "true");
  await page.getByTestId("component-gazebo").getByRole("button").first().click();
  await expect(page.getByTestId("component-gazebo")).toContainText("make sim-mac");
});

test("viewers: start Lichtblick web, links use the browser's host", async ({ page }) => {
  await signIn(page);
  await page.getByRole("tab", { name: "Viewers" }).click();
  await page.getByTestId("start-lichtblick").click();
  await jobSucceeded(page); // the job's log opens on the Log tab
  await page.getByRole("tab", { name: "Viewers" }).click();
  const link = page.getByTestId("open-lichtblick_web");
  await expect(link).toBeVisible({ timeout: 10_000 });
  await expect(link).toHaveAttribute("href", "http://127.0.0.1:8080/?ds=foxglove-websocket&ds.url=ws://127.0.0.1:8765");
  await expect(page.getByTestId("viewers-card")).toContainText("ws://127.0.0.1:8765");
});

test("stop: make down, and the state survives a reload", async ({ page }) => {
  await signIn(page);
  await page.getByTestId("env-stop").click();
  await expect(page.getByTestId("env-job")).toContainText("make down");
  await jobSucceeded(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("STOPPED", { timeout: 10_000 });
  // A stack started from the CLI shows up without any cockpit action.
  patchState({ stack: { service: "mac", ar3_mode: "sim", started: 1 } });
  await page.reload();
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · GAZEBO", { timeout: 10_000 });
  await expect(page.getByTestId("env-current")).toContainText("make sim-mac");
  patchState({ stack: null });
});

test("REAL: the pre-flight wizard arms after an e-stop test", async ({ page }) => {
  test.setTimeout(90_000);
  fakeState({ platform: "ubuntu-real", stack: { service: "dev", ar3_mode: "real", started: 1 } });
  await signIn(page);
  await expect(page.getByTestId("arm-chip")).toHaveText("NOT HOMED", { timeout: 10_000 });
  await page.getByTestId("open-preflight").click();
  await expect(page).toHaveURL(/\/environment\/preflight\/$/);
  await page.getByTestId("pf-devices-run").click();
  await expect(page.getByTestId("pf-devices-detail")).toContainText("present");

  await page.getByTestId("pf-estop-start").click();
  await expect(page.getByTestId("pf-estop-press")).toBeVisible();
  patchState({ estop: true }); // the operator presses the e-stop
  await expect(page.getByTestId("arm-chip")).toHaveText("E STOP", { timeout: 10_000 });
  await page.getByTestId("pf-estop-reset").click();
  await expect(page.getByTestId("pf-estop-detail")).toContainText("press and release seen");

  await page.getByTestId("pf-workspace-check").click();
  await page.getByTestId("pf-workspace-confirm").click();
  await page.getByTestId("pf-homing-run").click();
  await expect(page.getByTestId("pf-arm")).toHaveText("HOMED");
  await expect(page.getByTestId("robot-pose")).toBeVisible();
  await page.getByTestId("pf-pose-confirm").click();
  await expect(page.getByTestId("pf-velocity")).toHaveText("0.2");
  await page.getByTestId("pf-limits-confirm").click();
  await page.getByTestId("pf-arm-button").click();
  await expect(page.getByTestId("pf-arm")).toHaveText("ARMED");
  await expect(page.getByTestId("arm-chip")).toHaveText("ARMED", { timeout: 10_000 });

  // An e-stop while armed sends the wizard back to the e-stop test.
  patchState({ estop: true });
  await expect(page.getByTestId("pf-arm")).toHaveText("E STOP", { timeout: 10_000 });
  await expect(page.getByTestId("pf-estop-start")).toBeVisible();
  fakeState({});
});

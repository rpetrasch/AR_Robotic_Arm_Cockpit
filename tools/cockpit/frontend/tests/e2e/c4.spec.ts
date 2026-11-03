import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

// Phase C4 in fake mode (docs/specs/spec_cockpit.md §10, §12a, §18): overview, REC, /rosout errors
// searchable, live plots at 10 Hz, ROS graph and parameters, a killed container raising
// container_down with a red header within 5 s, alerts acknowledged and resolved, the jobs list
// with re-run, and the history with a Parquet export. Runs after c1 (user).
const STATE = path.join(__dirname, "..", "..", ".e2e-data", "fake", "state.json");

function fakeState(state: object) {
  mkdirSync(path.dirname(STATE), { recursive: true });
  writeFileSync(STATE, JSON.stringify(state));
}

function patchState(patch: (s: any) => void) {
  const s = JSON.parse(readFileSync(STATE, "utf8"));
  patch(s);
  writeFileSync(STATE, JSON.stringify(s));
}

async function signIn(page: Page, next = "/monitoring/") {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password").fill("e2e-long-password");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(new RegExp(next.replace(/[?]/g, "\\?")));
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() =>
  fakeState({
    images: { "ar3-dev": true, "ar3-dev-vnc": true },
    stack: { service: "mac", ar3_mode: "sim", started: Date.now() / 1000 },
    rosout: [["ERROR", "move_group", "Unable to solve the planning problem (e2e)"]],
  }),
);

test("overview and REC", async ({ page }) => {
  await signIn(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · GAZEBO", { timeout: 10_000 });
  await expect(page.getByTestId("tile-mode")).toContainText("SIM · GAZEBO");
  await expect(page.getByTestId("host-metrics")).toContainText("Memory");
  await expect(page.getByTestId("container-metrics")).toContainText("ar3-mac-1", { timeout: 10_000 });
  const rec = page.getByTestId("rec-toggle");
  await expect(rec).toHaveAttribute("data-recording", "true", { timeout: 10_000 }); // TELEMETRY_RECORD_SIM=1
  await rec.click();
  await expect(rec).toHaveAttribute("data-recording", "false", { timeout: 5_000 });
  await rec.click();
  await expect(rec).toHaveAttribute("data-recording", "true", { timeout: 5_000 });
});

test("/rosout errors are searchable", async ({ page }) => {
  await signIn(page, "/monitoring/?tab=logs");
  await page.getByTestId("log-search").fill("planning problem");
  const line = page.getByTestId("log-line").filter({ hasText: "Unable to solve the planning problem (e2e)" });
  await expect(line.first()).toBeVisible({ timeout: 15_000 });
  await expect(line.first()).toHaveAttribute("data-level", "ERROR");
  await expect(line.first()).toContainText("rosout");
  await expect(line.first()).toContainText("move_group");
  // live tail: a new line appears without reloading
  patchState((s) => s.rosout.push(["ERROR", "move_group", "planning problem number two (e2e)"]));
  await expect(page.getByTestId("log-line").filter({ hasText: "planning problem number two" })).toBeVisible({ timeout: 10_000 });
  const dl = await page.getByTestId("log-download").getAttribute("href");
  expect(dl).toContain("download=1");
});

test("live joint plots at 10 Hz", async ({ page }) => {
  await signIn(page, "/monitoring/?tab=telemetry");
  const chart = page.getByTestId("chart-positions");
  await expect(chart).toBeVisible();
  await expect.poll(async () => Number(await chart.getAttribute("data-points")), { timeout: 15_000 }).toBeGreaterThan(30);
  const rate = async () => Number(/([\d.]+) Hz/.exec((await page.getByTestId("live-rate").textContent()) ?? "")?.[1]);
  await expect.poll(rate, { timeout: 15_000 }).toBeGreaterThan(9); // the rate of the live samples
  const hz = await rate();
  expect(hz).toBeLessThan(11);
  await expect(page.getByTestId("topic-rates")).toContainText("/joint_states");
  await expect(page.getByTestId("camera-cam_overhead")).toBeVisible();
  await page.getByTestId("freeze").click();
  const frozen = await chart.getAttribute("data-points");
  await page.waitForTimeout(1500);
  expect(await chart.getAttribute("data-points")).toBe(frozen);
});

test("ROS graph and node parameters", async ({ page }) => {
  await signIn(page, "/monitoring/?tab=graph");
  await expect(page.getByTestId("graph-topics")).toContainText("/joint_states", { timeout: 10_000 });
  await page.getByTestId("node-/move_group").click();
  await expect(page.getByTestId("node-params")).toContainText("use_sim_time");
  await expect(page.getByTestId("node-graph")).toContainText("/display_planned_path");
});

test("a killed container: container_down within 5 s, header red; ack; make down resolves", async ({ page }) => {
  await signIn(page);
  await expect(page.getByTestId("status-pill")).toHaveText(/OK/, { timeout: 10_000 });
  patchState((s) => {
    s.stack.state = "exited"; // what `docker kill ar3-mac-1` leaves
    s.stack.exit_code = 137;
  });
  const killed = Date.now();
  await expect(page.getByTestId("status-pill")).toHaveText(/ERROR/, { timeout: 5_000 });
  const took = Date.now() - killed;
  expect(took).toBeLessThan(5_000);
  await expect(page.getByText("Container down: ros").first()).toBeVisible({ timeout: 5_000 }); // toast
  await page.goto("/monitoring/?tab=alerts");
  const alert = page.getByTestId("alert-container_down");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Exited (137)");
  await alert.getByTestId("ack").click();
  await expect(alert).toHaveAttribute("data-status", "acknowledged");
  patchState((s) => {
    s.stack = null; // make down removes the container
  });
  await expect(page.getByTestId("no-alerts")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("status-pill")).toHaveText(/OFFLINE/);
});

test("jobs list with re-run", async ({ page }) => {
  patchState((s) => {
    s.stack = { service: "mac", ar3_mode: "sim", started: Date.now() / 1000 };
  });
  await signIn(page, "/monitoring/?tab=jobs");
  const csrf = (await page.context().cookies()).find((c) => c.name === "csrftoken")?.value ?? "";
  const started = await page.request.post("/api/jobs", {
    data: { step_id: "rosbag.record", params: { duration: "1" } },
    headers: { "X-CSRFToken": csrf },
  });
  expect(started.status()).toBe(201);
  await expect(page.getByTestId("job-row").filter({ hasText: "make bag" }).first()).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("jobs-search").fill("make");
  await expect(page.getByTestId("job-row").first()).toContainText("make");
});

test("history: the session and a Parquet export", async ({ page }) => {
  await signIn(page, "/monitoring/?tab=telemetry");
  await page.getByTestId("tab-history").click();
  await expect(page.getByTestId("session-detail")).toBeVisible({ timeout: 10_000 });
  await expect.poll(async () => Number(/(\d+) samples/.exec((await page.getByTestId("session-samples").textContent()) ?? "")?.[1] ?? 0), {
    timeout: 15_000,
  }).toBeGreaterThan(50);
  await page.getByTestId("export-parquet").click();
  await expect(page.getByTestId("env-job-status")).toHaveText("succeeded", { timeout: 30_000 });
  await expect(page.getByTestId("export-row").first()).toContainText("joint_samples.parquet", { timeout: 10_000 });
});

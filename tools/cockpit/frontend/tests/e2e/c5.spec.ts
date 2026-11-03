import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

// Phase C5 in fake mode (docs/specs/spec_cockpit.md §12, §18 C5): the scenario list, the editor
// (form, scene, YAML with validation), Check reachability reporting an unreachable pose,
// a run in SIM·GAZEBO with live steps and evaluated success, the history and compare, a
// failing and an aborted run, pack-and-go. Runs after c1 (user).
const DATA = path.join(__dirname, "..", "..", ".e2e-data");
const STATE = path.join(DATA, "fake", "state.json");

function patchState(patch: (s: any) => void) {
  mkdirSync(path.dirname(STATE), { recursive: true });
  let s: any = {};
  try {
    s = JSON.parse(readFileSync(STATE, "utf8"));
  } catch {
    s = {};
  }
  patch(s);
  writeFileSync(STATE, JSON.stringify(s));
}

async function signIn(page: Page, next: string) {
  await page.goto(`/login/?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password").fill("e2e-long-password");
  await page.getByTestId("login-submit").click();
  await page.waitForURL((u) => u.pathname === next.split("?")[0]);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() =>
  patchState((s) => {
    s.images = { "ar3-dev": true, "ar3-dev-vnc": true };
    s.stack = { service: "mac", ar3_mode: "sim", started: Date.now() / 1000 };
    s.runs = {};
    delete s.run_fail_step;
  }),
);

test("the list: examples, search, gated task types", async ({ page }) => {
  await signIn(page, "/simulations/");
  await expect(page.getByTestId("scenario-tour-three-objects")).toContainText("Tour over three objects");
  await expect(page.getByTestId("run-sort-three-colors")).toBeDisabled(); // sort: SPEC Phase 3
  await page.getByTestId("sim-search").fill("unreach");
  await expect(page.getByTestId("scenario-tour-three-objects")).toHaveCount(0);
  await expect(page.getByTestId("scenario-unreachable-demo")).toBeVisible();
});

test("check reachability reports the unreachable pose before a run", async ({ page }) => {
  await signIn(page, "/simulations/edit/?id=unreachable-demo");
  await expect(page.getByTestId("validity")).toHaveText("valid");
  await page.getByTestId("check-reachability").click();
  await expect(page.getByTestId("reachability")).toContainText("Not every pose target is reachable");
  await expect(page.getByTestId("reach-1")).toContainText("NO_IK_SOLUTION");
});

test("editor: form and YAML stay in sync, invalid YAML blocks saving, scene editor adds objects", async ({ page }) => {
  await signIn(page, "/simulations/edit/");
  await page.getByTestId("f-id").fill("e2e-scenario");
  await page.getByTestId("f-name").fill("E2E scenario");
  await page.getByTestId("tab-yaml").click();
  const editor = page.getByTestId("yaml-editor");
  await expect(editor).toContainText("id: e2e-scenario");
  await expect(editor).toContainText("name: E2E scenario");
  await page.getByTestId("tab-scene").click();
  await page.getByTestId("add-box").click();
  await page.getByTestId("add-sphere").click();
  await expect(page.getByTestId("obj-box_1")).toBeVisible();
  await page.getByTestId("tab-yaml").click();
  await expect(editor).toContainText("id: box_1");
  // break it in the YAML: the form validation shows the line, Save is disabled
  await page.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.type("\nrepeat: lots");
  await expect(page.getByTestId("validity")).toContainText("error", { timeout: 5000 });
  await expect(page.getByTestId("problems")).toContainText("repeat");
  await expect(page.getByTestId("save")).toBeDisabled();
  await page.keyboard.press("Backspace", { delay: 10 });
  for (let i = 0; i < "repeat: lots".length; i++) await page.keyboard.press("Backspace");
  await expect(page.getByTestId("validity")).toHaveText("valid", { timeout: 5000 });
  await page.getByTestId("save").click();
  await expect(page.getByTestId("editor-msg")).toContainText("Saved e2e-scenario as v");
  expect(readFileSync(path.join(DATA, "scenarios", "e2e-scenario.yaml"), "utf8")).toContain("box_1");
});

test("a 5-step run with 3 spawned objects in SIM·GAZEBO: live steps, success evaluated, history", async ({ page }) => {
  await signIn(page, "/simulations/run/?id=tour-three-objects");
  await page.getByTestId("run-start").click();
  const live = page.getByTestId("live-run");
  await expect(live).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("env-job")).toContainText("make scenario-run ID=tour-three-objects");
  await expect(page.getByTestId("run-status")).toHaveText("succeeded", { timeout: 30_000 });
  for (let i = 0; i < 5; i++) await expect(page.getByTestId(`run-step-${i}`)).toHaveAttribute("data-status", "succeeded");
  await expect(page.getByTestId("run-evaluation")).toContainText("all_steps_succeed: every step succeeded");
  await expect(page.getByTestId("history-row").first()).toContainText("succeeded");
  await expect(page.getByTestId("env-job")).toContainText("scene: 3 spawned");
});

test("a failing step, then compare the two runs", async ({ page }) => {
  patchState((s) => (s.run_fail_step = 2));
  await signIn(page, "/simulations/run/?id=tour-three-objects");
  await page.getByTestId("run-start").click();
  await expect(page.getByTestId("run-status")).toHaveText("failed", { timeout: 30_000 });
  await expect(page.getByTestId("run-step-2")).toHaveAttribute("data-status", "failed");
  await expect(page.getByTestId("run-evaluation")).toContainText("step 3: planning failed");
  patchState((s) => delete s.run_fail_step);
  const picks = page.getByTestId("compare-pick");
  await picks.nth(0).click();
  await picks.nth(1).click();
  await expect(page.getByTestId("compare")).toContainText("move_pose (0.1, -0.1, 0.12) pilz_lin");
});

test("abort a run", async ({ page }) => {
  patchState((s) => (s.run_step_s = 3));
  await signIn(page, "/simulations/run/?id=tour-three-objects");
  await page.getByTestId("run-start").click();
  await expect(page.getByTestId("run-abort")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("run-abort").click();
  await expect(page.getByTestId("run-status")).toHaveText("aborted", { timeout: 30_000 });
  patchState((s) => delete s.run_step_s);
});

test("pack-and-go: pack two scenarios, unpack the zip with a decision", async ({ page }) => {
  await signIn(page, "/simulations/");
  await page.getByTestId("scenario-tour-three-objects").getByRole("checkbox").check();
  await page.getByTestId("scenario-unreachable-demo").getByRole("checkbox").check();
  const download = page.waitForEvent("download");
  await page.getByTestId("pack").click();
  const zip = await download;
  const file = path.join(DATA, "pack-e2e.zip");
  await zip.saveAs(file);
  expect(zip.suggestedFilename()).toMatch(/^ar3-scenarios-.*\.zip$/);
  await page.getByTestId("unpack-file").setInputFiles(file);
  await expect(page.getByTestId("unpack-tour-three-objects")).toContainText("unchanged");
  await page.getByTestId("unpack-apply").click();
  await expect(page.getByTestId("sim-msg")).toContainText("tour-three-objects: kept");
});

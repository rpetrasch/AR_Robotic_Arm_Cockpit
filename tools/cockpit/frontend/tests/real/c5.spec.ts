import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Phase C5 acceptance on a real Mac (docs/specs/spec_cockpit.md §18 C5), real Gazebo (make sim-mac):
// "Check reachability" reports the unreachable pose of unreachable-demo before running; the
// 5-step sequence tour-three-objects with 3 spawned objects runs from the UI in SIM·GAZEBO,
// its success is evaluated, the history shows the run. Then make down.
// `npx playwright test -c playwright.real.config.ts tests/real/c5.spec.ts`
const REPO = path.join(__dirname, "..", "..", "..", "..", "..");
const DATA = path.join(__dirname, "..", "..", ".e2e-data");

async function account(page: Page) {
  await page.goto("/setup-account/");
  await page.getByLabel("Username").fill("acceptance");
  await page.getByLabel("Password", { exact: true }).fill("acceptance-pass-1");
  await page.getByLabel("Repeat password").fill("acceptance-pass-1");
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(/\/setup\/$/);
}

test("C5 on a real Gazebo stack", async ({ page }) => {
  test.setTimeout(15 * 60_000);
  await account(page);
  await page.goto("/environment/");
  await page.getByTestId("robot-gazebo").click();
  await expect(page.getByTestId("start-command")).toHaveText("make sim-mac DETACH=1");
  await page.getByTestId("start").click();
  await expect(page.getByTestId("env-job-status")).toHaveText("succeeded", { timeout: 300_000 });

  // an unreachable pose is reported before running
  await page.goto("/simulations/edit/?id=unreachable-demo");
  await expect(page.getByTestId("validity")).toHaveText("valid");
  await page.getByTestId("check-reachability").click();
  await expect(page.getByTestId("reachability")).toContainText("Not every pose target is reachable", { timeout: 30_000 });
  await expect(page.getByTestId("reach-1")).toContainText("NO_IK_SOLUTION");
  console.log(`reachability: ${await page.getByTestId("reachability").innerText()}`);

  // the 5-step sequence with 3 spawned objects
  await page.goto("/simulations/run/?id=tour-three-objects");
  await page.getByTestId("run-start").click();
  await expect(page.getByTestId("live-run")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("run-status")).toHaveText(/succeeded|failed|aborted/, { timeout: 300_000 });
  const result = await page.getByTestId("run-status").textContent();
  const runs = await (await page.request.get("/api/scenarios/tour-three-objects/runs")).json();
  const run = runs[0];
  console.log(`run ${run.id}: ${result}, ${run.duration_s?.toFixed(1)} s, mode ${run.mode}`);
  for (const s of run.steps) console.log(`  ${s.index + 1}. ${s.label}: ${s.status} (${s.planning_s ?? "-"} s planning, ${s.execution_s ?? "-"} s execution)`);
  for (const c of run.evaluation) console.log(`  criterion ${c.criterion}: ${c.status} (${c.detail})`);
  console.log(`  final poses: ${JSON.stringify(run.metrics.object_poses)}`);
  expect(result).toBe("succeeded");
  expect(run.mode).toBe("sim_gazebo");
  expect(run.steps.map((s: { status: string }) => s.status)).toEqual(Array(5).fill("succeeded"));
  expect(Object.keys(run.metrics.object_poses).sort()).toEqual(["ball_blue", "cube_red", "cylinder_green"]);
  await expect(page.getByTestId("env-job")).toContainText("3 spawned in Gazebo");
  await expect(page.getByTestId("run-evaluation")).toContainText("all_steps_succeed: every step succeeded");
  await expect(page.getByTestId("history-row").first()).toContainText("succeeded");

  execFileSync("make", ["down"], { cwd: REPO, env: { ...process.env, COCKPIT_DATA_DIR: DATA } });
});

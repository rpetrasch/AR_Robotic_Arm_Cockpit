import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Phase C3 acceptance on a real Mac (docs/specs/spec_cockpit.md §18): the four §3.2 variants start
// and stop from the UI and the shown command is the one that ran; a second stack is refused
// with "stop and switch"; a stack started from the CLI is detected; Lichtblick web and noVNC
// answer; the header's software stop halts a running motion in Gazebo.
const REPO = path.join(__dirname, "..", "..", "..", "..", "..");
const TOKEN = path.join(__dirname, "..", "..", ".e2e-data", "agent.token");
const VARIANTS = [
  { robot: "mock", rviz: false, chip: "SIM · MOCK", command: "make up-mac DETACH=1" },
  { robot: "gazebo", rviz: false, chip: "SIM · GAZEBO", command: "make sim-mac DETACH=1" },
  { robot: "mock", rviz: true, chip: "SIM · MOCK", command: "make vnc DETACH=1" },
  { robot: "gazebo", rviz: true, chip: "SIM · GAZEBO", command: "AR3_MODE=sim make vnc DETACH=1 LICHTBLICK=1" },
];

function sh(cmd: string, args: string[]): string {
  return execFileSync(cmd, args, { cwd: REPO, encoding: "utf8", env: { ...process.env, COCKPIT_DATA_DIR: path.dirname(TOKEN) } });
}

async function account(page: Page) {
  await page.goto("/setup-account/");
  await page.getByLabel("Username").fill("acceptance");
  await page.getByLabel("Password", { exact: true }).fill("acceptance-pass-1");
  await page.getByLabel("Repeat password").fill("acceptance-pass-1");
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(/\/setup\/$/);
  await page.goto("/environment/");
}

async function waitJob(page: Page, timeout = 300_000) {
  await expect(page.getByTestId("env-job-status")).toHaveText(/succeeded|failed|timeout|cancelled/, { timeout });
  await expect(page.getByTestId("env-job-status")).toHaveText("succeeded");
}

test.describe.configure({ mode: "serial" });

test("four Mac variants: start, shown command = run command, stop and switch, stop", async ({ page }) => {
  await account(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("STOPPED", { timeout: 15_000 });
  for (const [i, v] of VARIANTS.entries()) {
    await page.reload();
    await page.getByTestId(`robot-${v.robot}`).click();
    if (v.rviz) await page.getByTestId("rviz").click();
    if (v.command.includes("LICHTBLICK")) await page.getByTestId("lichtblick").click();
    const shown = i === 0 ? v.command : `make down && ${v.command}`;
    await expect(page.getByTestId("start-command")).toHaveText(shown);
    await page.getByTestId("start").click();
    if (i > 0) await page.getByTestId("confirm-switch").click();
    await expect(page.getByTestId("env-job")).toContainText(shown);
    await waitJob(page);
    await expect(page.getByTestId("mode-chip")).toHaveText(v.chip, { timeout: 15_000 });
    await expect(page.getByTestId("env-ready")).toBeVisible({ timeout: 15_000 });
    const pill = await page.getByTestId("status-pill").textContent();
    console.log(`${v.command}: ready, pill ${pill}`);
  }
  // The last variant: noVNC and Lichtblick web answer through the links.
  await page.getByRole("tab", { name: "Viewers" }).click();
  for (const id of ["novnc", "lichtblick_web"]) {
    const href = await page.getByTestId(`open-${id}`).getAttribute("href");
    const res = await page.request.get(href!);
    expect(res.status(), href!).toBe(200);
    console.log(`${id}: ${href} -> ${res.status()}`);
  }
  expect(await (await page.request.get("http://127.0.0.1:8080/")).text()).toContain("Lichtblick");
});

test("a stack started from the CLI is detected; a second start is refused", async ({ page }) => {
  await page.goto("/login/");
  await page.getByLabel("Username").fill("acceptance");
  await page.getByLabel("Password").fill("acceptance-pass-1");
  await page.getByTestId("login-submit").click();
  sh("make", ["down"]);
  sh("make", ["sim-mac", "DETACH=1"]); // in a terminal, outside the cockpit
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · GAZEBO", { timeout: 30_000 });
  await expect(page.getByTestId("env-current")).toContainText("make sim-mac");
  const csrf = (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
  const res = await page.request.post("/api/env/start", { data: { mode: "sim_mock" }, headers: { "X-CSRFToken": csrf } });
  expect(res.status()).toBe(409);
  expect((await res.json()).title).toBe("A ROS stack is running");
  await expect(page.getByTestId("env-ready")).toBeVisible({ timeout: 120_000 });
});

test("the header's software stop halts a running motion in Gazebo", async ({ page }) => {
  await page.goto("/login/");
  await page.getByLabel("Username").fill("acceptance");
  await page.getByLabel("Password").fill("acceptance-pass-1");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("stop-button")).toBeEnabled({ timeout: 30_000 });
  const exec = (cmd: string) =>
    sh("docker", ["compose", "-f", "docker/compose.yaml", "exec", "-T", "mac", "bash", "-ic", cmd]);
  const joints = () =>
    // the output may start with "A message was lost!!!": take the first array anywhere
    exec("ros2 topic echo --once --field position /joint_states")
      .match(/array\('d', \[([^\]]+)\]/)![1]
      .split(",")
      .map(Number);
  // A slow move in the background (5 % speed: several seconds), then the header's Stop.
  const mover = execFileSync("bash", ["-c", "docker compose -f docker/compose.yaml exec -d -T mac bash -ic " +
    "'ros2 run ar3_bringup move_to_named stow --ros-args -p velocity_scaling:=0.05 > /tmp/mover.log 2>&1; echo $? > /tmp/mover.rc'"], { cwd: REPO });
  expect(mover).toBeDefined();
  const start = joints();
  await page.waitForTimeout(8000);
  const moving = joints();
  expect(Math.max(...moving.map((q, i) => Math.abs(q - start[i])))).toBeGreaterThan(0.02);
  await page.getByTestId("stop-button").click();
  await expect(page.getByText(/Software stop: [1-9]\d* goal\(s\) cancelled/)).toBeVisible();
  await page.waitForTimeout(1000);
  const held = joints();
  await page.waitForTimeout(3000);
  const later = joints();
  const drift = Math.max(...later.map((q, i) => Math.abs(q - held[i])));
  console.log(`moved ${JSON.stringify(moving)}; held drift ${drift}`);
  expect(drift).toBeLessThan(1e-3);
  await page.waitForTimeout(2000);
  expect(exec("cat /tmp/mover.rc").trim()).not.toBe("0");
  expect(readFileSync(TOKEN, "utf8").length).toBeGreaterThan(20);
});

test.afterAll(() => {
  if (!process.env.KEEP_STACK) sh("make", ["down"]); // KEEP_STACK=1: look at it afterwards
});

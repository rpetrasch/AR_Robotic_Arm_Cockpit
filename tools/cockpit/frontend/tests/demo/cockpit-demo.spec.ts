import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { Recorder } from "./recorder";

// The storyboard of the demo videos (tools/demo/README.md). It drives the real cockpit
// (e2e server, its own empty database, port 8401) against real Docker on this host, records
// the browser with Recorder, and leaves frames + events for tools/demo/render.py.
//
// `make demo-video` runs it and renders. Environment:
//   DEMO_OUT=<dir>       recording directory (default build/demo/<timestamp>)
//   DEMO_THEME=dark      the GUI in dark mode (playwright.demo.config.ts; default light)
//   DEMO_REPO_URL=<url>  shown on the closing card
//   DEMO_KEEP_STACK=1    leave the stack running afterwards
const REPO = path.join(__dirname, "..", "..", "..", "..", "..");
const FRONTEND = path.join(__dirname, "..", "..");
const DATA = path.join(FRONTEND, ".e2e-data");
const ENV = { ...process.env, COCKPIT_DATA_DIR: DATA };
const OUT = process.env.DEMO_OUT ?? path.join(REPO, "build", "demo", new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19));
const USER = { name: "demo", password: "demo-pass-2026" };
const CHAPTERS = 9;
// The arm has to move through Simulations, Monitoring, RViz and Lichtblick: one tour is 1–3 min
// of wall time, depending on Gazebo's real-time factor under the load of the viewers.
const REPEATS = 3;

function sh(cmd: string, args: string[]): string {
  return execFileSync(cmd, args, { cwd: REPO, encoding: "utf8", env: ENV });
}

/** Scroll the page smoothly by `dy` pixels. In the page: under the load of the stack and the
 * viewers, a mouse wheel event per step takes seconds instead of milliseconds. */
async function scroll(page: Page, dy: number, ms = 2500) {
  await page.evaluate(
    ([dy, ms]) =>
      new Promise<void>((done) => {
        const from = window.scrollY;
        const t0 = performance.now();
        const tick = (now: number) => {
          const f = Math.min(1, (now - t0) / ms);
          window.scrollTo(0, from + dy * f);
          if (f < 1) requestAnimationFrame(tick);
          else done();
        };
        requestAnimationFrame(tick);
      }),
    [dy, ms],
  );
}

/** Click a viewer's Open button: its own browser tab, which stays connected from now on. */
async function openViewer(page: Page, rec: Recorder, testId: string): Promise<Page> {
  const [tab] = await Promise.all([page.context().waitForEvent("page"), page.getByTestId(testId).click()]);
  await rec.use(tab);
  return tab;
}

function card(title: string, subtitle: string, footer = ""): string {
  const logo = readFileSync(path.join(FRONTEND, "public", "brand", "ar3-cockpit-mark.svg"), "utf8");
  return `<!doctype html><html><body style="margin:0;height:100vh;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:18px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
    background:radial-gradient(circle at 30% 20%,#1e293b,#0b1020 70%);color:#e2e8f0">
    <div style="width:150px;height:150px">${logo.replace("<svg ", '<svg width="150" height="150" ')}</div>
    <div style="font-size:64px;font-weight:800;letter-spacing:-1px">
      <span style="background:linear-gradient(90deg,#0d6efd,#6f42c1);-webkit-background-clip:text;color:transparent">AR3</span> Cockpit</div>
    <div style="font-size:26px;opacity:.85;max-width:1150px;text-align:center">${subtitle}</div>
    <div style="font-size:20px;opacity:.6;margin-top:24px">${footer}</div>
    <div style="display:none">${title}</div></body></html>`;
}

test("AR3 Cockpit demo recording", async ({ page }) => {
  test.setTimeout(60 * 60_000);

  // --- preparation (not recorded): no stack running, a fresh user, a stale workspace
  sh("make", ["down"]);
  await page.goto("/setup-account/");
  await page.getByLabel("Username").fill(USER.name);
  await page.getByLabel("Password", { exact: true }).fill(USER.password);
  await page.getByLabel("Repeat password").fill(USER.password);
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(/\/setup\/$/);
  await page.getByTestId("recheck-all").click();
  await page.waitForTimeout(8000); // the checks finish before the recording starts
  // Alerts from before the recording (e.g. the logs of a stack an earlier, failed run left
  // running) are not part of the demo: acknowledge them so the header starts clean.
  const csrf = (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
  const open = await (await page.request.get("/api/alerts?status=open")).json();
  const ids = open.alerts.map((a: { id: number }) => a.id);
  if (ids.length) {
    await page.request.post("/api/alerts", { data: { ids, comment: "before the demo recording" }, headers: { "X-CSRFToken": csrf } });
  }
  await page.context().clearCookies();

  const rec = new Recorder(OUT);
  await page.setContent(card("intro", "Install, run, watch and test an AR3 robot arm with ROS 2, MoveIt and Gazebo, from one web app."));
  await rec.start(page);
  rec.gif(2.5);
  rec.speed(1);
  await page.waitForTimeout(3500);

  // --- sign in
  await rec.goto("/login/");
  rec.caption("The AR3 Cockpit runs on your own computer, macOS or Ubuntu.");
  await page.waitForTimeout(1500);
  await page.getByLabel("Username").fill(USER.name);
  await page.getByLabel("Password").fill(USER.password);
  await page.getByTestId("login-submit").click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });

  // --- 1. Install & Setup
  await rec.goto("/setup/");
  rec.chapter(1, CHAPTERS, "Install & Setup", 3);
  await rec.say("Install & Setup checks everything the stack needs: host, Docker, images, workspace, tests and viewers.");
  await scroll(page, 900, 2500);
  await page.waitForTimeout(800);

  // --- 2. Environment
  await rec.goto("/environment/");
  rec.chapter(2, CHAPTERS, "Environment", 4);
  await rec.say("Environment starts and stops a ROS stack, in the modes that work on this host.");
  await page.getByTestId("robot-gazebo").click();
  await page.waitForTimeout(700);
  await page.getByTestId("rviz").click();
  await page.waitForTimeout(700);
  await page.getByTestId("lichtblick").click();
  await expect(page.getByTestId("start-command")).toContainText("make vnc");
  await rec.say("Gazebo physics, RViz in the browser and Lichtblick. Below the form: the exact command, to run it yourself.");
  await page.getByTestId("start").click();
  rec.caption("The stack starts: Gazebo, ros2_control, MoveIt, foxglove_bridge, noVNC and Lichtblick web.");
  rec.speed(6);
  rec.gif(12);
  await rec.lapse(500);
  await expect(page.getByTestId("env-job-status")).toHaveText(/succeeded/, { timeout: 10 * 60_000 });
  await expect(page.getByTestId("env-ready")).toBeVisible({ timeout: 3 * 60_000 });
  await rec.live();
  rec.gif(4);
  await rec.say("Ready. The header shows the mode, the status and links to the viewers, on every page.");
  await page.getByTestId("tab-components").click();
  // The whole diagram, just below the header. Only once it is drawn: until then the card is short.
  const diagram = page.getByTestId("diagram");
  await expect(diagram.getByTestId("node-move_group")).toBeVisible({ timeout: 15_000 });
  await scroll(page, (await diagram.boundingBox())!.y - 70, 1200);
  await page.waitForTimeout(300);
  const box = (await diagram.boundingBox())!;
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize()!.height);
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  rec.chapter(3, CHAPTERS, "What runs where", 1.5);
  await rec.say("What runs where: every component of this mode, in its layer, and how they connect.", 7000);

  // --- 4. Both viewers, each in its own tab
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await page.getByTestId("tab-viewers").click();
  rec.chapter(4, CHAPTERS, "Viewers", 3);
  await rec.say("The viewers: RViz with MoveIt's planning panel in the browser, and Lichtblick connected to the robot.");
  // Scale the 1600×900 desktop into the tab: at 1280×720 RViz's panels do not fit.
  await page.getByTestId("open-novnc").evaluate((a: HTMLAnchorElement) => {
    const url = new URL(a.href);
    url.searchParams.set("resize", "scale");
    a.href = url.toString();
  });
  const rviz = await openViewer(page, rec, "open-novnc");
  await rviz.waitForTimeout(5000);
  await rec.say("RViz runs in the ROS container and appears in the browser through noVNC.");
  await rec.use(page);
  await page.waitForTimeout(800);
  const lichtblick = await openViewer(page, rec, "open-lichtblick_web");
  await lichtblick.waitForTimeout(6000);
  await lichtblick.keyboard.press("Escape"); // a first-run dialog, if any
  await rec.say("Lichtblick connects to foxglove_bridge: the robot model, TF frames and joint states, live.");
  await rec.use(page);

  // --- 5. Simulations
  await rec.goto("/simulations/run/?id=tour-three-objects");
  rec.chapter(5, CHAPTERS, "Simulations", 3);
  await rec.say("Tour over three objects: three objects are spawned in Gazebo, then the arm moves over them, step by step.");
  await page.getByLabel("Repeat").fill(String(REPEATS));
  await page.getByTestId("run-start").click();
  await expect(page.getByTestId("live-run")).toBeVisible({ timeout: 30_000 });
  await rec.say("Each step is planned and executed, and the 3D view follows the arm. Now the same run from three other angles.", 12_000);

  // --- 6. Monitoring: the telemetry of the run
  await rec.goto("/monitoring/?tab=telemetry");
  rec.chapter(6, CHAPTERS, "Monitoring", 3);
  await expect
    .poll(async () => Number(await page.getByTestId("chart-positions").getAttribute("data-points")), { timeout: 60_000 })
    .toBeGreaterThan(30);
  await rec.say("Monitoring shows the telemetry of the run at 10 Hz: joint positions and velocities, commanded against actual.", 8000);
  rec.poster();
  await scroll(page, 900, 4000);
  await rec.say("Further down: the robot in 3D, the cameras and the topic rates. Everything is also recorded in TimescaleDB.");
  await scroll(page, -900, 1500);

  // --- 7. The same movement in RViz
  rec.chapter(7, CHAPTERS, "RViz", 3);
  await rec.use(rviz);
  await rec.say("The same run in RViz: MoveIt's plans and the arm following them, with the objects in the planning scene.", 22_000);

  // --- 8. The same movement in Lichtblick
  rec.chapter(8, CHAPTERS, "Lichtblick", 3);
  await rec.use(lichtblick);
  await rec.say("And in Lichtblick, on the same live data.", 22_000);

  // --- 9. ROS graph
  await rec.use(page);
  rec.chapter(9, CHAPTERS, "ROS graph", 3);
  await page.evaluate(() => window.scrollTo({ top: 0 }));
  await page.getByTestId("tab-graph").click();
  await expect(page.getByTestId("graph-topics")).toContainText("/joint_states", { timeout: 15_000 });
  await rec.say("The ROS graph of the running stack: nodes, topics, services and actions.");
  await page.getByTestId("node-/move_group").click();
  await expect(page.getByTestId("node-params")).toContainText("use_sim_time", { timeout: 15_000 });
  await page.getByTestId("node-detail").evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "start" }));
  await rec.say("Click a node: move_group with its publishers, subscribers and parameters, read live.", 5000);

  // A failed move in the recording must not end up in the README.
  const runs = await (await page.request.get("/api/scenarios/tour-three-objects/runs")).json();
  expect(runs.filter((r: { status: string }) => !["succeeded", "running"].includes(r.status))).toEqual([]);

  // --- closing card
  rec.clear();
  await page.setContent(card("outro", "Source available • ROS 2 Jazzy • RViz • MoveIt 2 • Gazebo Harmonic • Lichtblick", process.env.DEMO_REPO_URL ?? ""));
  await rec.live();
  rec.gif(1.5);
  await page.waitForTimeout(4000);
  await rec.stop();
  console.log(`recording: ${OUT}`);
});

// Also after a failed recording: a stack left running would put its errors into the next one.
test.afterEach(() => {
  if (process.env.DEMO_KEEP_STACK !== "1") sh("make", ["down"]);
});

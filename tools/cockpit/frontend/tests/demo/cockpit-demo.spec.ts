import { execFileSync, spawn } from "node:child_process";
import { readFileSync, utimesSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { Recorder } from "./recorder";

// The storyboard of the demo videos (tools/demo/README.md). It drives the real cockpit
// (e2e server, its own empty database, port 8401) against real Docker on this host, records
// the browser with Recorder, and leaves frames + events for tools/demo/render.py.
//
// `make demo-video` runs it and renders. Environment:
//   DEMO_OUT=<dir>       recording directory (default build/demo/<timestamp>)
//   DEMO_EXECUTE=0       skip the real Install & Setup steps (workspace build, tests)
//   DEMO_TESTS=0         run the workspace build but not `make test`
//   DEMO_THEME=dark      the GUI in dark mode (playwright.demo.config.ts; default light)
//   DEMO_REPO_URL=<url>  shown on the closing card
//   DEMO_KEEP_STACK=1    leave the stack running afterwards
const REPO = path.join(__dirname, "..", "..", "..", "..", "..");
const FRONTEND = path.join(__dirname, "..", "..");
const DATA = path.join(FRONTEND, ".e2e-data");
const ENV = { ...process.env, COCKPIT_DATA_DIR: DATA };
const OUT = process.env.DEMO_OUT ?? path.join(REPO, "build", "demo", new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19));
const EXECUTE = process.env.DEMO_EXECUTE !== "0";
const TESTS = process.env.DEMO_TESTS !== "0";
const SERVICE = process.env.DEMO_SERVICE ?? "vnc"; // the compose service of RViz-in-the-browser stacks
const USER = { name: "demo", password: "demo-pass-2026" };
const CHAPTERS = 6;

function sh(cmd: string, args: string[]): string {
  return execFileSync(cmd, args, { cwd: REPO, encoding: "utf8", env: ENV });
}

/** A command in the running stack's ROS container; resolves when it ends. */
function inStack(command: string): Promise<number> {
  return new Promise((resolve) => {
    const p = spawn("docker", ["compose", "-f", "docker/compose.yaml", "exec", "-T", SERVICE, "bash", "-ic", command], {
      cwd: REPO,
      env: ENV,
      stdio: "inherit",
    });
    p.on("exit", (code) => resolve(code ?? 1));
  });
}

const randomMoves = (count: number, seed: number, home = false) =>
  inStack(`python3 /ar3_ws/tools/demo/random_moves.py --count ${count} --seed ${seed}${home ? " --home" : ""}`);

/** Scroll the page (or the element under the pointer) smoothly by `dy` pixels. */
async function scroll(page: Page, dy: number, ms = 2500) {
  const steps = Math.max(1, Math.round(ms / 40));
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, dy / steps);
    await page.waitForTimeout(40);
  }
}

function card(title: string, subtitle: string, footer = ""): string {
  const logo = readFileSync(path.join(FRONTEND, "public", "brand", "ar3-cockpit-mark.svg"), "utf8");
  return `<!doctype html><html><body style="margin:0;height:100vh;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:18px;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
    background:radial-gradient(circle at 30% 20%,#1e293b,#0b1020 70%);color:#e2e8f0">
    <div style="width:150px;height:150px">${logo.replace("<svg ", '<svg width="150" height="150" ')}</div>
    <div style="font-size:64px;font-weight:800;letter-spacing:-1px">
      <span style="background:linear-gradient(90deg,#0d6efd,#6f42c1);-webkit-background-clip:text;color:transparent">AR3</span> Cockpit</div>
    <div style="font-size:26px;opacity:.85;max-width:900px;text-align:center">${subtitle}</div>
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
  await rec.say("The AR3 Cockpit runs on your own computer, macOS or Ubuntu. The login screen shows the simulated arm and the real one.");
  await page.getByLabel("Username").pressSequentially(USER.name, { delay: 70 });
  await page.getByLabel("Password").pressSequentially(USER.password, { delay: 50 });
  await page.getByTestId("login-submit").click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });

  // --- 1. Install & Setup
  await rec.goto("/setup/");
  rec.chapter(1, CHAPTERS, "Install & Setup", 3);
  await rec.say("Install & Setup checks everything the stack needs: host, Docker, images, workspace, tests and viewers.");
  await page.getByTestId("recheck-all").click();
  await page.mouse.move(640, 450);
  await scroll(page, 900, 4000);
  await rec.say("Every check shows the exact command behind it, so you can also run it yourself in a terminal.");
  const cli = page.getByTestId("cli-images.dev");
  await cli.scrollIntoViewIfNeeded();
  await cli.click();
  await page.waitForTimeout(2500);
  await scroll(page, -2000, 1500);

  if (EXECUTE) {
    rec.gif(false);
    // A source file changed: the workspace check notices and offers the rebuild.
    const changed = path.join(REPO, "src", "ar3_bringup", "package.xml");
    const now = new Date();
    utimesSync(changed, now, now);
    await rec.say("We changed a source file. After a re-check the cockpit says the workspace needs a rebuild, and offers to run it.");
    await page.getByTestId("recheck-all").click();
    const ws = page.getByTestId("check-ws.built");
    await expect(ws).toHaveAttribute("data-status", "warn", { timeout: 30_000 });
    await ws.scrollIntoViewIfNeeded();
    await page.waitForTimeout(2500);
    await page.getByTestId("fix-ws.built").click();
    await expect(page.getByTestId("job-page")).toBeVisible();
    await rec.say("The step runs as a job with a live log. Known errors are matched and explained.");
    rec.caption("colcon builds the workspace inside the dev container.");
    rec.speed(8);
    await rec.lapse(500);
    await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", /succeeded|failed/, { timeout: 20 * 60_000 });
    await rec.live();
    await rec.say("Done. The check is green again.");

    if (TESTS) {
      await rec.goto("/setup/");
      const tests = page.getByTestId("check-tests.last");
      const status = await tests.getAttribute("data-status");
      if (status === "warn" || status === "fail") {
        await tests.scrollIntoViewIfNeeded();
        await rec.say("The test check offers to run the unit and integration tests.");
        await page.getByTestId("fix-tests.last").click();
      } else {
        const csrf = (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
        const res = await page.request.post("/api/jobs", { data: { step_id: "tests.run" }, headers: { "X-CSRFToken": csrf } });
        await rec.goto(`/setup/job/?id=${(await res.json()).id}`);
      }
      await expect(page.getByTestId("job-page")).toBeVisible();
      rec.caption("make test: unit tests and mock integration tests, the same as in CI.");
      rec.speed(20);
      await rec.lapse(1000);
      await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", /succeeded|failed/, { timeout: 40 * 60_000 });
      await rec.live();
      await rec.say("The tests pass. The checklist is complete.");
    }
    rec.gif(3);
  }

  // --- 2. Environment
  await rec.goto("/environment/");
  rec.chapter(2, CHAPTERS, "Environment", 4);
  await rec.say("Environment starts and stops a ROS stack. The cockpit detects the host and offers only the modes that work on it.");
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
  await expect(page.getByTestId("env-job-status")).toHaveText(/succeeded|failed|timeout/, { timeout: 10 * 60_000 });
  await expect(page.getByTestId("env-ready")).toBeVisible({ timeout: 3 * 60_000 });
  await rec.live();
  rec.gif(4);
  await rec.say("Ready. The header shows the mode, the status and links to the viewers, on every page.");
  await page.getByTestId("tab-components").click();
  await rec.say("What runs where: every component of this mode, with its ports and the commands to start, stop and inspect it.");
  await page.mouse.move(640, 500);
  await scroll(page, 700, 3500);
  await page.waitForTimeout(1500);
  await scroll(page, -700, 1500);
  await page.getByTestId("tab-viewers").click();
  await rec.say("The viewers: RViz with MoveIt's planning panel in the browser, and Lichtblick connected to the robot.");
  const novnc = await page.getByTestId("open-novnc").getAttribute("href");
  const lichtblick = await page.getByTestId("open-lichtblick_web").getAttribute("href");

  // --- 3. RViz
  rec.chapter(3, CHAPTERS, "RViz in the browser", 3);
  const vnc = new URL(novnc!);
  vnc.searchParams.set("autoconnect", "1");
  vnc.searchParams.set("resize", "scale");
  await rec.goto(vnc.toString());
  await page.waitForTimeout(5000);
  await rec.say("RViz runs in the ROS container and appears in the browser through noVNC.");
  rec.caption("A script sends MoveIt the goal 'home': RViz shows the plan, Gazebo executes it.");
  await inStack("ros2 run ar3_bringup move_to_named home");
  await page.waitForTimeout(1000);
  rec.caption("Now random joint goals: MoveIt plans collision-free paths and the arm follows.");
  await randomMoves(3, 7);
  await page.waitForTimeout(1500);

  // --- 4. Lichtblick
  rec.chapter(4, CHAPTERS, "Lichtblick", 3);
  await rec.goto(lichtblick!);
  await page.waitForTimeout(6000);
  await page.keyboard.press("Escape"); // a first-run dialog, if any
  rec.caption("Lichtblick connects to foxglove_bridge: the robot model, TF frames, joint states and camera images, live.");
  await randomMoves(3, 11);
  await page.waitForTimeout(1500);

  // --- 5. Monitoring
  await rec.goto("/monitoring/?tab=telemetry");
  rec.chapter(5, CHAPTERS, "Monitoring", 3);
  const moving = randomMoves(5, 23, true);
  await expect
    .poll(async () => Number(await page.getByTestId("chart-positions").getAttribute("data-points")), { timeout: 60_000 })
    .toBeGreaterThan(30);
  await rec.say("Monitoring shows the telemetry at 10 Hz: joint positions and velocities, commanded against actual.");
  rec.poster();
  await page.mouse.move(640, 500);
  await scroll(page, 600, 4000);
  await rec.say("Further down: the robot in 3D, the cameras and the topic rates. Everything is also recorded in TimescaleDB.");
  await scroll(page, 900, 5000);
  await page.waitForTimeout(3000);
  await scroll(page, -1500, 2000);
  await moving;

  // --- 6. Simulations
  await rec.goto("/simulations/edit/?id=tour-three-objects");
  rec.chapter(6, CHAPTERS, "Simulations", 4);
  await rec.say("Simulations are MoveIt scenarios: objects on the table, a start pose and a sequence of steps, edited as a form or as YAML.");
  await page.waitForTimeout(2000);
  await rec.goto("/simulations/run/?id=tour-three-objects");
  await rec.say("Tour over three objects: three objects are spawned in Gazebo, then the arm moves over them, step by step.");
  await page.getByTestId("run-start").click();
  await expect(page.getByTestId("live-run")).toBeVisible({ timeout: 30_000 });
  rec.caption("Each step is planned and executed; the run is evaluated against its success criteria.");
  rec.speed(2);
  rec.gif(6);
  await expect(page.getByTestId("run-status")).toHaveText(/succeeded|failed|aborted/, { timeout: 10 * 60_000 });
  rec.gif(3);
  await rec.say("Succeeded. The run is kept in the history, with timings per step, to compare runs.");
  await page.getByTestId("run-history").scrollIntoViewIfNeeded();
  await page.waitForTimeout(2500);

  // --- closing card
  rec.clear();
  await page.setContent(card("outro", "Open source (MIT) · ROS 2 Jazzy · MoveIt 2 · Gazebo Harmonic", process.env.DEMO_REPO_URL ?? ""));
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

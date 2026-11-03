import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Phase C4 acceptance on a real Mac (docs/specs/spec_cockpit.md §18 C4), against real Docker and a
// real Gazebo stack (make sim-mac), ~13 min:
// - joint plots live at 10 Hz;
// - /rosout errors searchable;
// - a 10-min SIM session stores joints at 50 Hz without gaps (count = expected ± 1 %),
//   compresses after a policy run (make telemetry-compress ALL=1), exports to Parquet that
//   pandas reads;
// - killing a container raises container_down within 5 s and the header turns red.
// `npx playwright test -c playwright.real.config.ts tests/real/c4.spec.ts`
const REPO = path.join(__dirname, "..", "..", "..", "..", "..");
const DATA = path.join(__dirname, "..", "..", ".e2e-data");
const BACKEND = path.join(REPO, "tools", "cockpit", "backend");
const ENV = { ...process.env, COCKPIT_DATA_DIR: DATA, COCKPIT_DB_NAME: "ar3cockpit_e2e", COCKPIT_EXPORT_DIR: path.join(DATA, "exports") };
const SESSION_S = 600;

function sh(cmd: string, args: string[], cwd = REPO): string {
  return execFileSync(cmd, args, { cwd, encoding: "utf8", env: ENV });
}

async function account(page: Page) {
  await page.goto("/setup-account/");
  await page.getByLabel("Username").fill("acceptance");
  await page.getByLabel("Password", { exact: true }).fill("acceptance-pass-1");
  await page.getByLabel("Repeat password").fill("acceptance-pass-1");
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(/\/setup\/$/);
}

test.describe.configure({ mode: "serial" });

test("C4 on a real Gazebo stack", async ({ page }) => {
  test.setTimeout(30 * 60_000);
  await account(page);
  await page.goto("/environment/");
  await page.getByTestId("robot-gazebo").click();
  await expect(page.getByTestId("start-command")).toHaveText("make sim-mac DETACH=1");
  await page.getByTestId("start").click();
  await expect(page.getByTestId("env-job-status")).toHaveText("succeeded", { timeout: 300_000 });
  await expect(page.getByTestId("rec-toggle")).toHaveAttribute("data-recording", "true", { timeout: 15_000 });
  const t0 = Date.now();

  // --- joint plots live at 10 Hz
  await page.goto("/monitoring/?tab=telemetry");
  const chart = page.getByTestId("chart-positions");
  await expect.poll(async () => Number(await chart.getAttribute("data-points")), { timeout: 30_000 }).toBeGreaterThan(50);
  const live = await (await page.request.get("/api/telemetry/live")).json();
  const steps = new Set<number>();
  for (let i = 1; i < live.samples.length; i++) steps.add(Math.round((live.samples[i].t - live.samples[i - 1].t) * 1000));
  const rateOf = async () => Number(/([\d.]+) Hz/.exec((await page.getByTestId("live-rate").textContent()) ?? "")?.[1]);
  await expect.poll(rateOf, { timeout: 30_000 }).toBeGreaterThan(9); // after ~10 live samples
  await page.waitForTimeout(5_000);
  const liveRate = await page.getByTestId("live-rate").textContent();
  console.log(`live: ${liveRate}; steps between samples (ms): ${[...steps].join(", ")}`);
  expect([...steps]).toEqual([100]);
  const hz = Number(/([\d.]+) Hz/.exec(liveRate ?? "")?.[1]);
  expect(hz).toBeGreaterThan(9.5);
  expect(hz).toBeLessThan(10.5);

  // --- /rosout errors searchable
  const marker = `C4 acceptance error ${Date.now()}`;
  sh("docker", ["exec", "ar3-mac-1", "bash", "-c",
    `source /opt/ros/jazzy/setup.bash && python3 -c "import rclpy, time; rclpy.init(); n = rclpy.create_node('c4_acceptance'); time.sleep(1); n.get_logger().error('${marker}'); time.sleep(1)"`]); // prettier-ignore
  await page.goto("/monitoring/?tab=logs");
  await page.getByTestId("log-search").fill(marker);
  const line = page.getByTestId("log-line").filter({ hasText: marker }).filter({ hasText: "rosout" });
  await expect(line.first()).toBeVisible({ timeout: 20_000 });
  await expect(line.first()).toHaveAttribute("data-level", "ERROR");
  const api = await (await page.request.get(`/api/logs?source=rosout&level=ERROR&q=${encodeURIComponent(marker)}`)).json();
  console.log(`rosout search: ${api.lines.length} line(s): ${api.lines[0]?.node}: ${api.lines[0]?.text}`);
  expect(api.lines[0].node).toBe("c4_acceptance");

  // --- a 10-min session at 50 Hz without gaps
  const session = (await (await page.request.get("/api/sessions/current")).json()).session;
  expect(session.rate_hz).toBe(50);
  const wait = SESSION_S * 1000 + 60_000 - (Date.now() - t0);
  console.log(`recording session ${session.id}; waiting ${Math.round(wait / 1000)} s`);
  await page.goto("/monitoring/");
  await page.waitForTimeout(Math.max(0, wait));
  const detail = await (await page.request.get(`/api/sessions/${session.id}`)).json();
  const j1 = detail.counts.per_joint.find((j: { joint: string }) => j.joint === "joint_1");
  // a 10-min window inside the session, away from its start and end
  const from = new Date(new Date(j1.from).getTime() + 30_000);
  const to = new Date(from.getTime() + SESSION_S * 1000);
  const win = await (
    await page.request.get(`/api/telemetry/joints?session=${session.id}&from=${from.toISOString()}&to=${to.toISOString()}&bucket=${SESSION_S}`)
  ).json();
  const count = win.series.joint_1.n.reduce((a: number, b: number) => a + b, 0);
  const expected = SESSION_S * 50;
  const gaps = await (await page.request.get(`/api/sessions/${session.id}/gaps`)).json();
  console.log(`10 min window ${from.toISOString()} … ${to.toISOString()}: joint_1 ${count} samples, expected ${expected} (${((100 * (count - expected)) / expected).toFixed(3)} %)`);
  console.log(`whole session: joint_1 ${j1.samples} samples, ${j1.rate_hz} Hz; gaps > 30 ms: ${gaps.gaps}; session dropped ${detail.dropped}`);
  expect(Math.abs(count - expected)).toBeLessThanOrEqual(expected * 0.01);
  expect(gaps.gaps).toBe(0);

  // --- compression after a policy run, then the Parquet export
  console.log(sh("make", ["telemetry-compress", "ALL=1"]));
  const storage = await (await page.request.get("/api/telemetry/storage")).json();
  const js = storage.tables.find((t: { table: string }) => t.table === "joint_samples");
  console.log(`joint_samples: ${js.compressed_chunks}/${js.chunks} chunks compressed, ${js.bytes_before} -> ${js.bytes_after} bytes`);
  expect(js.compressed_chunks).toBe(js.chunks);
  const again = await (
    await page.request.get(`/api/telemetry/joints?session=${session.id}&from=${from.toISOString()}&to=${to.toISOString()}&bucket=${SESSION_S}`)
  ).json();
  expect(again.series.joint_1.n.reduce((a: number, b: number) => a + b, 0)).toBe(count);

  await page.goto("/monitoring/?tab=telemetry");
  await page.getByTestId("tab-history").click();
  await page.getByTestId("export-parquet").click();
  await expect(page.getByTestId("env-job-status")).toHaveText("succeeded", { timeout: 120_000 });
  const exports = await (await page.request.get("/api/telemetry/export")).json();
  const dir = path.join(DATA, "exports", exports[0].name);
  const py = `import pandas as pd; df = pd.read_parquet("${dir}/joint_samples.parquet"); print(len(df), df["joint"].nunique(), df["time"].dt.tz, df["time"].min(), df["time"].max())`;
  const out = sh(path.join(BACKEND, ".venv", "bin", "python"), ["-c", py], BACKEND).trim();
  console.log(`pandas: ${out}`);
  const [rows, joints] = out.split(" ").map(Number);
  expect(joints).toBe(6);
  expect(rows).toBeGreaterThanOrEqual(6 * j1.samples);

  // --- killing a container: container_down within 5 s, header red
  await page.goto("/monitoring/");
  await expect(page.getByTestId("status-pill")).toHaveText(/OK|WARN/);
  const killed = Date.now();
  sh("docker", ["kill", "ar3-mac-1"]);
  await expect(page.getByTestId("status-pill")).toHaveText(/ERROR/, { timeout: 5_000 });
  const red = Date.now() - killed;
  const alerts = await (await page.request.get("/api/alerts?status=open")).json();
  const down = alerts.alerts.find((a: { rule: string }) => a.rule === "container_down");
  const raised = new Date(down.first_seen).getTime() - killed;
  console.log(`docker kill: header ERROR after ${red} ms; container_down raised after ${raised} ms: ${down.message}`);
  expect(raised).toBeLessThan(5_000);
  sh("make", ["down"]);
  await expect(page.getByTestId("status-pill")).toHaveText(/OFFLINE/, { timeout: 15_000 });
});

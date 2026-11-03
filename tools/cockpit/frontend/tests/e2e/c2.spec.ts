import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

// Phase C2 acceptance in fake mode (docs/specs/spec_cockpit.md §18): following only the checklist
// leads to all-green, and a forced failure shows the matched hint. Runs after c1.spec.ts,
// which created the user. The fake world starts as a fresh clone (core/fake.py).
const STATE = path.join(__dirname, "..", "..", ".e2e-data", "fake", "state.json");

function fakeState(state: object) {
  mkdirSync(path.dirname(STATE), { recursive: true });
  writeFileSync(STATE, JSON.stringify(state));
}

async function signIn(page: Page) {
  await page.goto("/login/");
  await page.getByLabel("Username").fill("owner");
  await page.getByLabel("Password").fill("e2e-long-password");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/environment\/$/);
}

test.beforeAll(() => fakeState({}));

test("following only the checklist leads to all-green", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.getByTestId("menu-setup").click();
  await page.getByTestId("recheck-all").click(); // c1 may have left results of another world
  await expect(page.getByTestId("check-images.dev")).toHaveAttribute("data-status", "fail");
  await expect(page.getByTestId("check-repo.env")).toHaveAttribute("data-status", "warn");
  await page.getByTestId("cli-images.dev").click();
  await expect(page.getByTestId("cli-text-images.dev")).toContainText("make image");

  const ran: string[] = [];
  for (let i = 0; i < 12; i++) {
    await page.getByTestId("continue-setup").click();
    const next = await Promise.race([
      page.waitForURL(/\/setup\/job\/\?id=/).then(() => "job"),
      page.getByTestId("setup-message").waitFor().then(() => "done"),
    ]);
    if (next === "done") break;
    await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", "succeeded", { timeout: 20_000 });
    ran.push((await page.locator("h1").textContent()) ?? "");
    await page.getByText("Back to Install & Setup").first().click();
    await expect(page.getByTestId("setup-summary")).toBeVisible();
  }
  await expect(page.getByTestId("setup-message")).toContainText("Everything the cockpit can fix is done");
  expect(ran).toEqual([
    "Create docker/.env",
    "Build the dev image",
    "Build the noVNC image",
    "Build the workspace",
    "Run the tests",
    "Simulation smoke test",
    "Pull the Lichtblick web image",
    "Pull an Ollama model",
    "Pull an Ollama model",
  ]);
  const done = await page.getByTestId("setup-done").textContent();
  const [a, b] = done!.match(/(\d+) \/ (\d+)/)!.slice(1);
  expect(a).toBe(b); // all-green
});

test("a busy port fails its check with the command that finds the program", async ({ page }) => {
  fakeState({ ports_busy: [8765] });
  await signIn(page);
  await page.goto("/setup/");
  await page.getByTestId("check-viewers.ports").getByRole("button", { name: "Re-check" }).click();
  await expect(page.getByTestId("check-viewers.ports")).toHaveAttribute("data-status", "fail");
  await expect(page.getByTestId("detail-viewers.ports")).toContainText("8765 (foxglove_bridge)");
  await page.getByTestId("howto-viewers.ports").click();
  await expect(page.getByTestId("fix-text-viewers.ports")).toContainText("lsof -nP -iTCP:8765 -sTCP:LISTEN");
});

test("a failing job shows the matched hint above the log", async ({ page }) => {
  const line = "Bind for 0.0.0.0:8765 failed: port is already allocated";
  fakeState({ fail: { "make smoke-sim": line } });
  await signIn(page);
  const csrf = (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
  const res = await page.request.post("/api/jobs", {
    data: { step_id: "sim.smoke" },
    headers: { "X-CSRFToken": csrf },
  });
  expect(res.status()).toBe(201);
  const job = await res.json();
  await page.goto(`/setup/job/?id=${job.id}`);
  await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", "failed", { timeout: 20_000 });
  await expect(page.getByTestId("hint-port_in_use")).toContainText("one stack per host");
  await expect(page.getByTestId("job-log").locator('[data-match="port_in_use"]')).toContainText(line);
  fakeState({});
});

test("a job log offers its warning and error lines only", async ({ page }) => {
  const line = "[move_group-4] [ERROR] [1.0] [moveit]: boom";
  fakeState({ fail: { "make smoke-sim": line } });
  await signIn(page);
  const csrf = (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
  const res = await page.request.post("/api/jobs", {
    data: { step_id: "sim.smoke" },
    headers: { "X-CSRFToken": csrf },
  });
  const job = await res.json();
  await page.goto(`/setup/job/?id=${job.id}`);
  await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", "failed", { timeout: 20_000 });
  await expect(page.getByTestId("level-bar")).toContainText("1 with errors, 0 with warnings");
  await page.getByTestId("show-levels").click();
  const rows = page.getByTestId("job-log").locator("[data-seq]");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("[ERROR]");
  await page.getByTestId("show-levels").click(); // back to the whole log
  expect(await rows.count()).toBeGreaterThan(1);
  fakeState({});
});

test("returning from a job scrolls back to the check that was fixed", async ({ page }) => {
  await signIn(page);
  await page.goto("/setup/");
  await expect(page.getByTestId("check-sim.smoke")).toBeAttached();
  await page.evaluate(() => sessionStorage.setItem("ar3cockpit.setup.focus", "sim.smoke"));
  await page.goto("/setup/");
  await expect(page.getByTestId("check-sim.smoke")).toBeInViewport();
});

test("Docker windows need X access: the check offers the fix, the Viewers tab the commands", async ({ page }) => {
  fakeState({ platform: "ubuntu", x11: false });
  await signIn(page);
  await page.goto("/setup/");
  const check = page.getByTestId("check-viewers.x11");
  await check.getByRole("button", { name: "Re-check" }).click(); // the list shows the last result
  await expect(check).toHaveAttribute("data-status", "warn");
  await expect(page.getByTestId("detail-viewers.x11")).toContainText("RViz and Gazebo windows would fail");
  await page.getByTestId("fix-viewers.x11").click();
  await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", "succeeded", { timeout: 20_000 });
  await expect(page.getByTestId("job-page")).toContainText("xhost +local:docker");
  await page.goto("/setup/");
  await expect(page.getByTestId("check-viewers.x11")).toHaveAttribute("data-status", "pass", { timeout: 10_000 });

  await page.goto("/environment/");
  await page.getByRole("tab", { name: "Viewers" }).click();
  const native = page.getByTestId("viewer-card-native");
  await expect(native).toContainText("native windows on this computer's desktop");
  await expect(native).toContainText("xhost +local:docker");
  await expect(native).toContainText("make sim-linux");
  fakeState({});
});

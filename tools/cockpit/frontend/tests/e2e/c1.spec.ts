import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

// Phase C1 acceptance in fake mode (docs/specs/spec_cockpit.md §18): first user, login, shell and
// header status. The tests share one fresh database and run in order.
const USER = "owner";
const PASSWORD = "e2e-long-password";
const STATE = path.join(__dirname, "..", "..", ".e2e-data", "fake", "state.json");

function fakeState(state: object) {
  mkdirSync(path.dirname(STATE), { recursive: true });
  writeFileSync(STATE, JSON.stringify({ docker: true, stack: null, arm: "NOT_HOMED", ...state }));
}

async function signIn(page: Page, password = PASSWORD, user = USER) {
  await page.goto("/login/");
  await page.getByLabel("Username").fill(user);
  await page.getByLabel("Password").fill(password);
  await page.getByTestId("login-submit").click();
}

test.beforeAll(() => fakeState({}));

test("a protected page sends you to the login, which offers the first-user setup", async ({ page }) => {
  await page.goto("/environment/");
  await expect(page).toHaveURL(/\/login\/\?next=/);
  await expect(page.getByTestId("first-run")).toBeVisible();
  await expect(page.getByTestId("login-bg")).toBeVisible();
  await expect(page.getByText("real video pending")).toBeVisible();
});

test("login panel: links to the project, no company branding, can be dragged", async ({ page }) => {
  await page.goto("/login/");
  const footer = page.getByTestId("login-project");
  await expect(footer).toHaveAttribute("href", "https://codeberg.org/AMDIS/AR_Robotic_Arm_Cockpit");
  await expect(footer).toContainText("Source available");
  await expect(page.locator('img[src*="amdisx"]')).toHaveCount(0);
  expect((await page.request.get("/brand/amdisx.png")).ok()).toBe(false);
  await expect(page.getByText(/powered by/i)).toHaveCount(0);

  const panel = page.getByTestId("login-panel");
  const before = (await panel.boundingBox())!;
  // grab the panel next to its fields, not on them
  await page.mouse.move(before.x + 12, before.y + 12);
  await page.mouse.down();
  await page.mouse.move(before.x - 150, before.y + 80, { steps: 5 });
  await page.mouse.up();
  const moved = (await panel.boundingBox())!;
  expect(moved.x).toBeCloseTo(before.x - 162, 0);
  expect(moved.y).toBeCloseTo(before.y + 68, 0);
  // it cannot be dragged off screen: at least 48 px stay visible
  await page.mouse.move(moved.x + 12, moved.y + 12);
  await page.mouse.down();
  await page.mouse.move(-3000, 5000, { steps: 5 });
  await page.mouse.up();
  const clamped = (await panel.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(clamped.x + clamped.width).toBeGreaterThanOrEqual(47);
  expect(clamped.y).toBeLessThanOrEqual(viewport.height - 47);
  // a double click on an empty spot centres it again
  // (on its visible right edge: the rest is off screen)
  await page.mouse.dblclick(clamped.x + clamped.width - 10, clamped.y + 12);
  const back = (await panel.boundingBox())!;
  expect(back.x).toBeCloseTo(before.x, 0);
  expect(back.y).toBeCloseTo(before.y, 0);
  // pressing in a field selects text, it does not drag (the first-user page: same panel)
  await page.goto("/setup-account/");
  await expect(page.getByTestId("login-project")).toBeVisible();
  const setupPanel = page.getByTestId("login-panel");
  const start = (await setupPanel.boundingBox())!;
  const fb = (await page.getByLabel("Username").boundingBox())!;
  await page.mouse.move(fb.x + 10, fb.y + fb.height / 2);
  await page.mouse.down();
  await page.mouse.move(fb.x + 200, fb.y + 150, { steps: 3 });
  await page.mouse.up();
  expect((await setupPanel.boundingBox())!.x).toBeCloseTo(start.x, 0);
});

test("first user: password rules, then created and signed in", async ({ page }) => {
  await page.goto("/setup-account/");
  await page.getByLabel("Username").fill(USER);
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByLabel("Repeat password").fill("short");
  await page.getByTestId("setup-submit").click();
  await expect(page.getByTestId("setup-error")).toContainText("at least 10 characters");
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Repeat password").fill(PASSWORD);
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(/\/setup\/$/);
  await expect(page.getByTestId("host-chip")).toHaveText("macOS 15.5 · arm64");
});

test("the setup page refuses a second first user", async ({ page }) => {
  await page.goto("/setup-account/");
  await expect(page.getByText("A user exists already.")).toBeVisible();
});

test("wrong password: generic message", async ({ page }) => {
  await signIn(page, "wrong-password");
  await expect(page.getByTestId("login-error")).toHaveText("Invalid username or password.");
});

test("header: host chip, fake banner, menu, STOPPED without a stack", async ({ page }) => {
  await signIn(page);
  await expect(page).toHaveURL(/\/environment\/$/);
  await expect(page.getByTestId("host-chip")).toHaveText("macOS 15.5 · arm64");
  await expect(page.getByTestId("fake-banner")).toBeVisible();
  await expect(page.getByTestId("status-pill")).toHaveText("OFFLINE");
  await expect(page.getByTestId("mode-chip")).toHaveText("STOPPED");
  await expect(page.getByTestId("stop-button")).toHaveCount(0);
  for (const id of ["menu-setup", "menu-environment", "menu-monitoring", "menu-simulations"]) {
    await expect(page.getByTestId(id)).toBeVisible();
  }
  await expect(page.getByTestId("mode-sim_gazebo_rviz")).toContainText("AR3_MODE=sim make vnc");
});

test("header follows a stack started outside the cockpit, live", async ({ page }) => {
  await signIn(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("STOPPED");
  fakeState({ stack: { service: "vnc", ar3_mode: "sim" } }); // as if `AR3_MODE=sim make vnc` ran
  await expect(page.getByTestId("mode-chip")).toHaveText("SIM · GAZEBO", { timeout: 10_000 });
  await expect(page.getByTestId("status-pill")).toHaveText("OK");
  await expect(page.getByTestId("viewer-novnc")).toHaveAttribute("href", /:6080\/vnc\.html/);
  await expect(page.getByTestId("stop-button")).toBeEnabled(); // the (fake) agent answers
  await expect(page.getByTestId("env-current")).toContainText("AR3_MODE=sim make vnc");
  fakeState({ docker: false });
  await expect(page.getByTestId("status-pill")).toHaveText("ERROR", { timeout: 10_000 });
  fakeState({});
});

test("settings: add and delete a user, audit shows it; you cannot delete yourself", async ({ page }) => {
  await signIn(page);
  await page.getByTestId("user-menu").click();
  await page.getByTestId("menu-settings").click();
  await expect(page.getByTestId(`user-${USER}`)).toBeVisible();
  await expect(page.getByTestId(`delete-${USER}`)).toBeDisabled();
  await page.getByLabel("Username").fill("bob");
  await page.getByLabel("Password", { exact: true }).fill("another-long-pass");
  await page.getByTestId("add-user").click();
  await expect(page.getByTestId("user-bob")).toBeVisible();
  await page.getByTestId("delete-bob").click();
  await page.getByTestId("confirm-delete").click();
  await expect(page.getByTestId("user-bob")).toHaveCount(0);
  await page.getByRole("tab", { name: "Audit log" }).click();
  await expect(page.getByTestId("audit-table")).toContainText("user.delete");
  await expect(page.getByTestId("audit-table")).toContainText("auth.login_failed");
});

test("theme: one click per choice, the active one checked, kept after a reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await signIn(page);
  const html = page.locator("html");
  const pick = async (mode: string) => {
    // A click while the previous menu is still closing does not reopen it.
    await expect(page.getByTestId("theme-system")).toHaveCount(0);
    await page.getByTestId("user-menu").click();
    await page.getByTestId(`theme-${mode}`).click();
  };
  await page.getByTestId("user-menu").click();
  await expect(page.getByTestId("theme-system").getByTestId("theme-active")).toBeVisible();
  await page.keyboard.press("Escape");
  await pick("dark");
  await expect(html).toHaveClass(/\bdark\b/);
  await pick("light");
  await expect(html).not.toHaveClass(/\bdark\b/);
  await pick("dark");
  await page.reload();
  await expect(html).toHaveClass(/\bdark\b/);
  await page.getByTestId("user-menu").click();
  await expect(page.getByTestId("theme-dark").getByTestId("theme-active")).toBeVisible();
  await expect(page.getByTestId("theme-active")).toHaveCount(1);
  await page.getByTestId("theme-system").click();
  await expect(html).not.toHaveClass(/\bdark\b/);
});

test("assistant drawer survives navigation; sign out", async ({ page }) => {
  await signIn(page);
  await page.getByTestId("assistant-toggle").click();
  await expect(page.getByTestId("assistant-drawer")).toContainText("cannot run anything");
  await page.getByTestId("menu-simulations").click();
  await expect(page.getByRole("heading", { name: "Simulations" })).toBeVisible();
  await expect(page.getByTestId("assistant-drawer")).toBeVisible();
  await page.getByTestId("user-menu").click();
  await page.getByTestId("menu-logout").click();
  await expect(page).toHaveURL(/\/login\/$/);
  await page.goto("/settings/");
  await expect(page).toHaveURL(/\/login\/\?next=/);
});

test("REAL host in fake mode shows the arm state", async ({ page }) => {
  fakeState({ stack: { service: "dev", ar3_mode: "real" }, arm: "NOT_HOMED" });
  await signIn(page);
  await expect(page.getByTestId("mode-chip")).toHaveText("REAL · AR3", { timeout: 10_000 });
  await expect(page.getByTestId("arm-chip")).toHaveText("NOT HOMED");
  fakeState({});
});

test("five failures throttle the login", async ({ page }) => {
  // Its own user name: the lock is per user and IP, and later specs sign in as USER.
  for (let i = 0; i < 5; i++) {
    await signIn(page, "wrong-password", "mallory");
    await expect(page.getByTestId("login-error")).toBeVisible();
  }
  await signIn(page, PASSWORD, "mallory");
  await expect(page.getByTestId("login-error")).toContainText("Too many attempts");
});

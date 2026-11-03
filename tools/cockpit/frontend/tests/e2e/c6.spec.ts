import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

// Phase C6 in fake mode (docs/specs/spec_cockpit.md §14, §18 C6): the assistant answers with the
// scripted provider over the real knowledge index (fake embeddings): a failed ws-build job
// asked "why did it fail?" (sources, reasoning hidden, a proposed step that runs after
// confirming), "AI: Explain warnings/errors", Explain this check / mode, the scenario help diff
// applied to the editor (not saved), the per-thread model switch and Settings → AI.
// Runs after c1 (user).
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

async function csrf(page: Page): Promise<string> {
  return (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
}

test.describe.configure({ mode: "serial" });

test.beforeAll(() =>
  patchState((s) => {
    s.images = { "ar3-dev": true, "ar3-dev-vnc": true };
    s.stack = { service: "mac", ar3_mode: "sim", started: Date.now() / 1000 };
    s.fail = { "make ws-build": "Failed   <<< ar3_bringup [1.2s, exited with code 1]" };
  }),
);

test.afterAll(() => patchState((s) => delete s.fail));

test("the index is built at start (fake embeddings)", async ({ page }) => {
  await signIn(page, "/settings/");
  await expect
    .poll(async () => (await (await page.request.get("/api/assistant/index")).json()).chunks, { timeout: 60_000 })
    .toBeGreaterThan(50);
  await page.getByTestId("tab-ai").click();
  await expect(page.getByTestId("ai-index-state")).toHaveText("full-text + vectors");
  await expect(page.getByTestId("ai-model-gpt-oss:20b")).toBeVisible();
  await expect(page.getByTestId("ai-task-chat")).toBeVisible();
});

test("why did ws-build fail? — sources, hidden reasoning, a proposed step", async ({ page }) => {
  await signIn(page, "/setup/");
  const res = await page.request.post("/api/jobs", { data: { step_id: "ws.build" }, headers: { "X-CSRFToken": await csrf(page) } });
  const job = await res.json();
  await page.goto(`/setup/job/?id=${job.id}`);
  await expect(page.getByTestId("job-page")).toHaveAttribute("data-status", "failed", { timeout: 30_000 });

  // a question typed in the drawer carries the open job as context
  await page.getByTestId("assistant-toggle").click();
  await page.getByTestId("assistant-input").fill("why did ws-build fail?");
  await page.getByTestId("assistant-send").click();
  const answer = page.getByTestId("assistant-answer").last();
  await expect(answer).toContainText("You asked: why did ws-build fail?", { timeout: 20_000 });
  await expect(answer.getByTestId("assistant-footer")).toContainText("fake · ");
  await expect(answer.getByTestId("assistant-reasoning")).toHaveCount(0); // hidden by default
  await answer.getByTestId("assistant-reasoning-toggle").click();
  await expect(answer.getByTestId("assistant-reasoning")).toContainText("The user asks");
  await answer.getByTestId("cite").first().click();
  await expect(answer.getByTestId("source-1")).toBeVisible();

  const thread = (await (await page.request.get("/api/assistant/threads")).json())[0];
  const full = await (await page.request.get(`/api/assistant/threads/${thread.id}`)).json();
  const msg = full.messages[1];
  expect(full.messages[0].context.job_id).toBe(job.id);
  expect(msg.sources.some((s: { source: string }) => s.source.startsWith("docs/"))).toBe(true);

  // the proposed step runs only after the confirmation, as a job
  await answer.getByTestId("proposal-ws.build").first().click();
  await expect(answer.getByTestId("proposal-confirm-ws.build").first()).toContainText("make ws-build");
  await answer.getByTestId("proposal-run-ws.build").first().click();
  await page.waitForURL((u) => u.pathname === "/setup/job/" && u.searchParams.get("id") !== job.id);
  await expect(page.getByTestId("job-page")).not.toHaveAttribute("data-status", "queued", { timeout: 10_000 });
  await expect(page.getByTestId("assistant-drawer")).toBeVisible(); // survives navigation
});

test("AI: Explain warnings/errors on a job", async ({ page }) => {
  await signIn(page, "/setup/");
  const jobs = await (await page.request.get("/api/jobs?step=ws.build&status=failed")).json();
  await page.goto(`/setup/job/?id=${jobs[0].id}`);
  await page.getByTestId("explain-ai").click();
  const answer = page.getByTestId("assistant-answer").last();
  for (const heading of ["Harmless", "Worth fixing", "Needs action"]) await expect(answer).toContainText(heading, { timeout: 20_000 });
  await expect(page.getByTestId("assistant-question").last()).toContainText("Explain warnings/errors");
  // the error panel's entry point asks about the matched line
  await page.getByTestId("ask-colcon_failed").click();
  await expect(page.getByTestId("assistant-answer").last()).toContainText("why did it happen", { timeout: 20_000 });
  // right-click on a log line
  await page.locator("[data-match='colcon_failed']").first().click({ button: "right" });
  await page.getByTestId("log-menu-ask").click();
  await expect(page.getByTestId("assistant-answer").last()).toContainText("What does this line", { timeout: 20_000 });
});

test("explain a check and a mode", async ({ page }) => {
  await signIn(page, "/setup/");
  await page.getByTestId("explain-images.dev").click();
  await expect(page.getByTestId("assistant-answer").last()).toContainText("Explain the check", { timeout: 20_000 });
  await page.goto("/environment/");
  await page.getByTestId("assistant-toggle").click();
  await page.getByTestId("explain-mode").click();
  await expect(page.getByTestId("assistant-answer").last()).toContainText("Explain this mode", { timeout: 20_000 });
});

test("help me write this scenario: a validated diff, applied but not saved", async ({ page }) => {
  await signIn(page, "/simulations/edit/");
  await page.goto("/simulations/edit/?id=tour-three-objects");
  await expect(page.getByTestId("validity")).toHaveText("valid");
  await page.getByTestId("scenario-help-input").fill("add a description");
  await page.getByTestId("scenario-help-ask").click();
  const card = page.getByTestId("assistant-scenario");
  await expect(card.getByTestId("assistant-scenario-validity")).toHaveText("valid scenario", { timeout: 20_000 });
  await expect(card.getByTestId("assistant-scenario-diff")).toContainText("+description: Written with the assistant");
  await card.getByTestId("assistant-scenario-apply").click();
  await expect(page.getByTestId("scenario-help-applied")).toBeVisible();
  const saved = await (await page.request.get("/api/scenarios/tour-three-objects")).json();
  expect(saved.yaml).not.toContain("Written with the assistant");
});

test("per-thread model switch; Settings → AI saves a task model", async ({ page }) => {
  await signIn(page, "/settings/");
  await page.getByTestId("assistant-toggle").click();
  await page.getByTestId("assistant-new").click();
  await page.getByTestId("assistant-model-toggle").click();
  await page.getByTestId("assistant-provider").click();
  await page.getByRole("option", { name: "Ollama" }).click();
  await page.getByTestId("assistant-model-select").click();
  await page.getByRole("option", { name: "gemma3:4b" }).click();
  await expect
    .poll(async () => (await (await page.request.get("/api/assistant/threads")).json())[0].model)
    .toBe("gemma3:4b");

  await page.getByTestId("tab-ai").click();
  const chat = page.getByTestId("ai-task-chat");
  await chat.getByTestId("ai-chat-model").click();
  await page.getByRole("option", { name: "gpt-oss:20b" }).click();
  await chat.getByTestId("ai-chat-save").click();
  await expect.poll(async () => (await (await page.request.get("/api/ai/tasks/chat")).json()).model).toBe("gpt-oss:20b");
  await chat.getByTestId("ai-chat-test").click();
  await expect(chat.getByTestId("ai-chat-result")).toContainText("fake · gpt-oss:20b");
  // back to the default for the other tests
  await page.request.put("/api/ai/tasks/chat", { data: { model: null }, headers: { "X-CSRFToken": await csrf(page) } });
});

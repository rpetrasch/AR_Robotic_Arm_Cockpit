import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Phase C6 acceptance on a real Mac (docs/specs/spec_cockpit.md §18 C6), with the real Ollama of
// this host (llama3.1:8b, gemma3:4b, gpt-oss:20b, bge-m3 pulled) and real Docker, ~10 min:
// - the knowledge index is built with bge-m3 embeddings (hybrid search);
// - "why did ws-build fail?" on a failed job cites the job log lines and a doc section (the
//   failure: a throwaway package with a CMake error, removed again afterwards);
// - "AI: Explain warnings/errors" on a succeeded `make test` job sorts the distinct lines into
//   harmless / worth fixing / needs action, with sources;
// - switching the chat model llama3.1 → gemma3 → gpt-oss applies without a restart; gpt-oss's
//   reasoning is stored but hidden;
// - Anthropic is not used without ASSISTANT_ALLOW_EXTERNAL=1 (Ollama answers, the footer says so).
// `npx playwright test -c playwright.real.config.ts tests/real/c6.spec.ts`
const REPO = path.join(__dirname, "..", "..", "..", "..", "..");
const BROKEN = path.join(REPO, "src", "zz_c6_broken");

async function signIn(page: Page) {
  await page.goto("/setup-account/");
  await page.getByLabel("Username").fill("acceptance");
  await page.getByLabel("Password", { exact: true }).fill("acceptance-pass-1");
  await page.getByLabel("Repeat password").fill("acceptance-pass-1");
  await page.getByTestId("setup-submit").click();
  await expect(page).toHaveURL(/\/setup\/$/);
}

async function csrf(page: Page): Promise<string> {
  return (await page.context().cookies()).find((c) => c.name === "csrftoken")!.value;
}

async function runJob(page: Page, step: string, timeout = 900_000): Promise<{ id: string; status: string }> {
  const job = await (await page.request.post("/api/jobs", { data: { step_id: step }, headers: { "X-CSRFToken": await csrf(page) } })).json();
  let current = job;
  await expect
    .poll(async () => (current = await (await page.request.get(`/api/jobs/${job.id}`)).json()).status, { timeout, intervals: [2000] })
    .toMatch(/succeeded|failed|timeout|cancelled/);
  return current;
}

type Event = { event: string; [k: string]: any };

/** Asks over the API (the SSE body arrives whole when the stream ends). */
async function ask(page: Page, content: string, task = "chat", context: Record<string, string> = {}, thread?: string) {
  const id = thread ?? (await (await page.request.post("/api/assistant/threads", { data: {}, headers: { "X-CSRFToken": await csrf(page) } })).json()).id;
  const res = await page.request.post(`/api/assistant/threads/${id}/messages`, {
    data: { content, task, context },
    headers: { "X-CSRFToken": await csrf(page) },
    timeout: 600_000,
  });
  expect(res.status()).toBe(200);
  const events: Event[] = (await res.text())
    .split("\n\n")
    .map((block) => {
      const name = block.match(/^event: (.+)$/m)?.[1];
      const data = block.match(/^data: (.+)$/m)?.[1];
      return name && data ? { event: name, ...JSON.parse(data) } : null;
    })
    .filter((e): e is Event => e !== null);
  const last = events[events.length - 1];
  if (last.event === "error") throw new Error(`assistant error: ${last.message}`);
  return { thread: id, meta: events[0], message: last.message };
}

test.describe.configure({ mode: "serial" });

test("C6 on this Mac with the real Ollama", async ({ page }) => {
  test.setTimeout(45 * 60_000);
  await signIn(page);

  // the index: built by run_jobs at start, with bge-m3 embeddings
  await expect
    .poll(async () => {
      const s = await (await page.request.get("/api/assistant/index")).json();
      return !!s.built && s.chunks > 50 && s.embedded === s.chunks; // `built`: the refresh finished
    }, { timeout: 300_000, intervals: [5000] })
    .toBe(true);
  const index = await (await page.request.get("/api/assistant/index")).json();
  console.log(`index: ${index.files} files, ${index.chunks} chunks, ${index.embed_model} (${index.dim}), ${index.duration_s} s`);

  // 1. why did ws-build fail?
  mkdirSync(BROKEN, { recursive: true });
  writeFileSync(path.join(BROKEN, "package.xml"), `<?xml version="1.0"?>
<package format="3"><name>zz_c6_broken</name><version>0.0.0</version>
<description>C6 acceptance: fails on purpose, removed by the test</description>
<maintainer email="none@example.com">test</maintainer><license>MIT</license>
<buildtool_depend>ament_cmake</buildtool_depend><export><build_type>ament_cmake</build_type></export></package>
`);
  writeFileSync(path.join(BROKEN, "CMakeLists.txt"), `cmake_minimum_required(VERSION 3.8)
project(zz_c6_broken)
find_package(ament_cmake REQUIRED)
find_package(ar3_missing_dependency REQUIRED)
ament_package()
`);
  let failed;
  try {
    failed = await runJob(page, "ws.build");
  } finally {
    rmSync(BROKEN, { recursive: true, force: true });
    for (const dir of ["build", "install", "log"]) rmSync(path.join(REPO, dir, "zz_c6_broken"), { recursive: true, force: true });
  }
  expect(failed.status).toBe("failed");
  await page.goto(`/setup/job/?id=${failed.id}`);
  await page.getByTestId("assistant-toggle").click();
  await page.getByTestId("assistant-input").fill("why did ws-build fail?");
  await page.getByTestId("assistant-send").click();
  const answer = page.getByTestId("assistant-answer").last();
  await expect(answer.getByTestId("assistant-footer")).toBeVisible({ timeout: 300_000 });
  const threads = await (await page.request.get("/api/assistant/threads")).json();
  const why = (await (await page.request.get(`/api/assistant/threads/${threads[0].id}`)).json()).messages[1];
  console.log(`--- why did ws-build fail? (${why.provider} ${why.model}, ${why.latency_ms} ms)\n${why.content}\nsources: ${why.sources.map((s: any) => `[${s.n}] ${s.source} — ${s.heading}`).join("; ")}`);
  expect(why.error).toBe("");
  expect(why.content).toMatch(/ar3_missing_dependency/); // the cause, from the job's log lines
  expect(why.content).toMatch(/\[\d+\]/); // a citation
  expect(why.sources.some((s: any) => s.source.startsWith("docs/"))).toBe(true); // a doc section
  await expect(answer.getByTestId("cite").first()).toBeVisible();

  // workspace green again for the next steps
  expect((await runJob(page, "ws.build")).status).toBe("succeeded");

  // 2. Explain warnings/errors on a succeeded make test job
  const tests = await runJob(page, "tests.run", 1_800_000);
  expect(tests.status).toBe("succeeded");
  await page.goto(`/setup/job/?id=${tests.id}`);
  await page.getByTestId("explain-ai").click();
  const explain = page.getByTestId("assistant-answer").last();
  await expect(explain.getByTestId("assistant-footer")).toBeVisible({ timeout: 600_000 });
  const t2 = await (await page.request.get("/api/assistant/threads")).json();
  const ex = (await (await page.request.get(`/api/assistant/threads/${t2[0].id}`)).json()).messages[1];
  console.log(`--- explain make test (${ex.provider} ${ex.model}, ${ex.latency_ms} ms)\n${ex.content}\nsources: ${ex.sources.map((s: any) => `[${s.n}] ${s.source}`).join("; ")}`);
  for (const heading of ["Harmless", "Worth fixing", "Needs action"]) expect(ex.content).toContain(heading);
  expect(ex.content).toMatch(/\[\d+\]/);

  // 3. switch the chat model without a restart; reasoning hidden
  for (const model of ["llama3.1:8b", "gemma3:4b", "gpt-oss:20b"]) {
    const res = await page.request.put("/api/ai/tasks/chat", { data: { provider: "ollama", model }, headers: { "X-CSRFToken": await csrf(page) } });
    expect(res.status()).toBe(200);
    const { meta, message } = await ask(page, "In one sentence: what does `make sim-mac` start?");
    console.log(`--- ${model}: ${message.latency_ms} ms, reasoning ${message.reasoning.length} chars\n${message.content}`);
    expect(meta.model).toBe(model);
    expect(message.model).toBe(model);
    expect(message.content.length).toBeGreaterThan(20);
    expect(message.content).not.toContain("<think>");
    if (model === "gpt-oss:20b") expect(message.reasoning.length).toBeGreaterThan(0);
  }
  await page.goto("/settings/");
  await page.getByTestId("assistant-toggle").click();
  const last = page.getByTestId("assistant-answer").last();
  await expect(last.getByTestId("assistant-footer")).toContainText("gpt-oss:20b");
  await expect(last.getByTestId("assistant-reasoning")).toHaveCount(0);

  // 4. Anthropic only with the flag
  await page.request.put("/api/ai/tasks/chat", { data: { provider: "anthropic", model: "claude-sonnet-5-5" }, headers: { "X-CSRFToken": await csrf(page) } });
  const fallback = await ask(page, "Say hello.");
  expect(fallback.meta.provider).toBe("ollama");
  expect(fallback.message.notes.join(" ")).toContain("ASSISTANT_ALLOW_EXTERNAL=1");
  await page.request.put("/api/ai/tasks/chat", { data: { provider: null, model: null }, headers: { "X-CSRFToken": await csrf(page) } });
});

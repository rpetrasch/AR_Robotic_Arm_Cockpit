/**
 * The assistant and the AI settings (docs/specs/spec_cockpit.md §14). Shapes mirror
 * backend/assistant/*.py. The answer streams as server-sent events from a POST, so it is read
 * with fetch + a stream reader (EventSource can only GET).
 */
import { ApiError, BackendDown, csrfToken, http, type Problem } from "./http";
import type { Job } from "./cockpit";

export type Task = "chat" | "error_explain" | "scenario_help";

/** What a page tells the assistant about itself; the server builds the context pack from it. */
export interface AskContext {
  page?: string;
  job_id?: string;
  match_id?: string;
  line?: string;
  check_id?: string;
  mode_id?: string;
  scenario_id?: string;
  yaml?: string;
}

export interface Source {
  n: number;
  source: string;
  heading: string;
  kind: "doc" | "config" | "knowledge" | "file";
}

export interface Proposal {
  kind: "step" | "make";
  id: string;
  title: string;
  step_id: string | null;
  command: string;
  params: boolean;
}

export interface ScenarioSuggestion {
  yaml: string | null;
  diff?: string;
  ok?: boolean;
  errors?: { path: string; message: string; line: number | null }[];
  warnings?: { path: string; message: string; line: number | null }[];
  error?: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  task: Task;
  content: string;
  reasoning: string;
  context: AskContext;
  sources: Source[];
  proposals: Proposal[];
  extra: { scenario?: ScenarioSuggestion; error?: { hint: string; unavailable: boolean } };
  provider: string;
  model: string;
  notes: string[];
  latency_ms: number | null;
  error: string;
  created: string;
}

export interface Thread {
  id: string;
  title: string;
  provider: string;
  model: string;
  created: string;
  updated: string;
  messages?: Message[];
}

export const listThreads = () => http<Thread[]>("/assistant/threads");
export const getThread = (id: string) => http<Thread>(`/assistant/threads/${id}`);
export const createThread = (body: { title?: string; provider?: string; model?: string } = {}) =>
  http<Thread>("/assistant/threads", { method: "POST", body });
export const updateThread = (id: string, body: { provider?: string; model?: string; title?: string }) =>
  http<Thread>(`/assistant/threads/${id}`, { method: "PATCH", body });
export const deleteThread = (id: string) => http<void>(`/assistant/threads/${id}`, { method: "DELETE" });

export type StreamEvent =
  | { event: "meta"; provider: string; model: string; task: Task; sources: Source[]; notes: string[]; retrieval: string }
  | { event: "token"; text: string }
  | { event: "reasoning"; text: string }
  | { event: "done"; message: Message; first_token_ms: number | null }
  | { event: "error"; message: string; hint: string; unavailable: boolean; docs: string; id: number };

/** Asks in a thread; calls `onEvent` per server-sent event until the stream ends. */
export async function ask(
  threadId: string,
  body: { content: string; task: Task; context: AskContext },
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/assistant/threads/${threadId}/messages`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-CSRFToken": csrfToken() ?? "" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (signal?.aborted) return;
    throw e instanceof DOMException ? e : new BackendDown();
  }
  if (!res.ok || !res.body) {
    let problem: Problem = { status: res.status, title: res.statusText };
    try {
      problem = { status: res.status, ...(await res.json()) };
    } catch {
      // not JSON: keep the status line
    }
    throw new ApiError(res.status, problem);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let name = "";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) name = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (name && data) onEvent({ event: name, ...JSON.parse(data) } as StreamEvent);
      }
    }
  } catch (e) {
    if (!signal?.aborted) throw e;
  }
}

// --- index ------------------------------------------------------------------------------------

export interface IndexStatus {
  embed_model?: string;
  dim?: number | null;
  files?: number;
  chunks: number;
  embedded: number;
  degraded: boolean;
  error?: string;
  built?: string;
  duration_s?: number;
  by_kind: Record<string, number>;
  excluded: string[];
}

export const getIndexStatus = () => http<IndexStatus>("/assistant/index");
export const rebuildIndex = (full = true) => http<Job>("/assistant/index/rebuild", { method: "POST", body: { full } });

// --- AI settings --------------------------------------------------------------------------------

export type AiTask = "chat" | "error_explain" | "scenario_help" | "embed";
export const AI_TASKS: AiTask[] = ["chat", "error_explain", "scenario_help", "embed"];

export interface TaskConfig {
  task: AiTask;
  provider: "ollama" | "anthropic";
  model: string;
  temperature: number;
  max_tokens: number;
  num_ctx: number;
  timeout_s: number;
  sources: Record<string, string>;
}

export interface OllamaModel {
  name: string;
  size: number;
  family: string;
  parameter_size: string;
  quantization: string;
  capabilities: string[];
  hint: { need_gb: number; fits: boolean; text: string };
}

export interface AiModels {
  ollama: { base_url: string; up: boolean; error: string; models: OllamaModel[] };
  anthropic: { allowed: boolean; flag: string; key: "set" | "not set"; default_model: string };
  memory: { ram_gb: number; gpus: { name: string; mem_gb: number }[] };
  fake: boolean;
  tasks: Record<AiTask, TaskConfig>;
}

export interface TestResult {
  ok: boolean;
  latency_ms?: number;
  first_token_ms?: number | null;
  text?: string;
  provider: string;
  model: string;
  notes?: string[];
  error?: string;
  hint?: string;
}

export const getAiModels = () => http<AiModels>("/ai/models");
export const saveTask = (task: AiTask, body: Partial<Record<keyof TaskConfig, string | number | null>>) =>
  http<TaskConfig>(`/ai/tasks/${task}`, { method: "PUT", body });
export const testTask = (task: AiTask) => http<TestResult>(`/ai/tasks/${task}/test`, { method: "POST" });

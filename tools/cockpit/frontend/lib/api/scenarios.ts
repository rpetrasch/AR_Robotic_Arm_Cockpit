/**
 * Simulations (docs/specs/spec_cockpit.md §12): scenarios, validation, reachability, runs, history,
 * compare, pack-and-go. Shapes mirror backend/scenarios/*.py.
 */
import { csrfToken, http } from "./http";
import type { Job } from "./cockpit";

export interface Problem {
  severity: "error" | "warning";
  path: string;
  message: string;
  line: number | null;
}

export interface ScenarioSummary {
  id: string;
  name: string;
  description: string;
  version: number | null;
  tags: string[];
  modes: string[];
  task_type: string | null;
  steps: number;
  objects: number;
  broken: boolean;
  last_run: { id: string; result: string; started: string; mode: string } | null;
}

export interface Bin {
  name: string;
  xyz: [number, number, number];
  yaw?: number;
  size: [number, number, number];
  wall?: number;
  color?: number[];
  ref?: string;
}

export interface SiteContext {
  table: { size: [number, number]; xyz: number[]; rpy: number[] };
  mount: { xyz: number[]; rpy: number[] };
  bins: Bin[];
  named_poses: Record<string, Record<string, Record<string, number>>>;
  joint_limits_deg: Record<string, [number, number]>;
  gripper_range_m: [number, number];
  motion: { velocity_scaling?: number; acceleration_scaling?: number };
  reach: { radius_m: number; source: string };
  worlds: string[];
  host_robots: string[];
  capabilities: { task_types?: string[]; step_types?: string[]; gazebo?: boolean } | null;
}

export interface RunStep {
  index: number;
  label: string;
  status: "pending" | "running" | "succeeded" | "failed" | "aborted";
  planning_s?: number;
  execution_s?: number;
  duration_s?: number;
  error?: string;
}

export interface Criterion {
  criterion: string;
  status: "pass" | "fail" | "skipped";
  detail: string;
}

export interface RunRecord {
  id: string;
  scenario: string;
  version: string;
  session: string;
  mode: string;
  user: string;
  status: "running" | "succeeded" | "failed" | "aborted";
  result: string;
  seed: number | null;
  repeat: [number, number];
  started: string;
  ended: string | null;
  duration_s: number | null;
  failed_step: number | null;
  error: string;
  steps: RunStep[];
  evaluation: Criterion[];
  metrics: { duration_s?: number; planning_s?: number; execution_s?: number; object_poses?: Record<string, { xyz: number[] }> };
  options: { keep_scene?: boolean; record_bag?: boolean };
  job_id: string | null;
}

export interface UnpackPreview {
  token: string;
  source_host: string;
  git_commit: string;
  created: string;
  config_warnings: string[];
  scenarios: {
    id: string;
    name: string;
    version: number;
    existing_version: number | null;
    status: "new" | "same" | "differs";
    diff: string;
    errors: Problem[];
    warnings: Problem[];
    default: "keep" | "replace" | "copy";
  }[];
}

type Checked = { ok: boolean; errors: Problem[]; warnings: Problem[]; data: Record<string, unknown> | null };

export const listScenarios = () => http<ScenarioSummary[]>("/scenarios");
export const getScenario = (id: string) =>
  http<{ id: string; yaml: string; data: Record<string, unknown>; errors: Problem[]; warnings: Problem[] }>(`/scenarios/${id}`);
export const createScenario = (yaml: string) => http<{ id: string; version: number }>("/scenarios", { method: "POST", body: { yaml } });
export const saveScenario = (id: string, yaml: string) =>
  http<{ id: string; version: number; warnings: Problem[] }>(`/scenarios/${id}`, { method: "PUT", body: { yaml } });
export const deleteScenario = (id: string) => http<{ deleted: string }>(`/scenarios/${id}?confirm=1`, { method: "DELETE" });
export const duplicateScenario = (id: string) => http<{ id: string; version: number }>(`/scenarios/${id}/duplicate`, { method: "POST" });
export const validateScenario = (yaml: string) => http<Checked>("/scenarios/validate", { method: "POST", body: { yaml } });
export const importScenario = (yaml: string, replace = false) =>
  http<{ id: string; version: number }>("/scenarios/import", { method: "POST", body: { yaml, replace } });
export const getSiteContext = () => http<SiteContext>("/scenarios/context");
export const exportUrl = (id: string) => `/api/scenarios/${id}/export`;
export const checkReachability = (id: string, yaml?: string) =>
  http<{ targets: ({ step: number; reachable: boolean; error?: string; x: number; y: number; z: number })[]; reachable: boolean }>(
    `/scenarios/${id}/reachability`,
    { method: "POST", body: yaml ? { yaml } : {} },
  );
export const startRun = (id: string, body: { mode?: string; repeat?: number; seed?: number | null; keep_scene?: boolean; record?: boolean }) =>
  http<Job>(`/scenarios/${id}/runs`, { method: "POST", body });
export const listRuns = (id: string) => http<RunRecord[]>(`/scenarios/${id}/runs`);
export const getRun = (id: string) => http<RunRecord>(`/runs/${id}`);
export const abortRun = (id: string) => http<RunRecord>(`/runs/${id}/abort`, { method: "POST" });
export const pauseRun = (id: string, paused: boolean) => http<{ paused: boolean }>(`/runs/${id}/pause`, { method: "POST", body: { paused } });
export const compareRuns = (a: string, b: string) =>
  http<{ a: RunRecord; b: RunRecord; steps: { index: number; label: string; a: Partial<RunStep>; b: Partial<RunStep> }[] }>(
    `/runs/compare?a=${a}&b=${b}`,
  );
export const unpackApply = (token: string, decisions: Record<string, string>) =>
  http<{ done: { id: string; action: string }[] }>("/scenarios/unpack/apply", { method: "POST", body: { token, decisions } });

/** The pack as a file download (POST with the selection). */
export async function downloadPack(ids: string[]): Promise<void> {
  const res = await fetch("/api/scenarios/pack", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() ?? "" },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "scenarios.zip";
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export async function unpackPreview(file: File): Promise<UnpackPreview> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/scenarios/unpack", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRFToken": csrfToken() ?? "" },
    body: form,
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
  return body;
}

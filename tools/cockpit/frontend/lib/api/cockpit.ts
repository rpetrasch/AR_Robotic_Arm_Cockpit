/**
 * Typed API calls. Request/response types come from schema.d.ts, generated from the backend's
 * OpenAPI (`npm run gen:api`). The status snapshot is a free-form object in the schema; its
 * shape is `Status` below and mirrors backend/environment/status.py.
 */
import { http } from "./http";
import type { AlertSummary } from "./monitoring";
import type { components } from "./schema";

type S = components["schemas"];
export type Me = S["Me"];
export type User = S["User"];
export type SetupStatus = S["SetupStatus"];
export type AuditPage = S["AuditPage"];
export type AuditEntry = S["AuditEntry"];

export const login = (username: string, password: string) =>
  http<Me>("/auth/login", { method: "POST", body: { username, password }, authRedirect: false });
export const logout = () => http<S["Ok"]>("/auth/logout", { method: "POST" });
export const getMe = () => http<Me>("/auth/me", { authRedirect: false });
export const changePassword = (old_password: string, new_password: string) =>
  http<S["Ok"]>("/auth/password", { method: "POST", body: { old_password, new_password } });

/** GET also sets the csrftoken cookie that the login and setup forms need. */
export const getSetupStatus = () => http<SetupStatus>("/setup-account", { authRedirect: false });
export const createFirstUser = (username: string, password: string) =>
  http<Me>("/setup-account", { method: "POST", body: { username, password }, authRedirect: false });

export const listUsers = () => http<User[]>("/users");
export const createUser = (username: string, password: string) =>
  http<User>("/users", { method: "POST", body: { username, password } });
export const deleteUser = (id: number) => http<S["Ok"]>(`/users/${id}`, { method: "DELETE" });
export const resetPassword = (id: number, password: string) =>
  http<S["Ok"]>(`/users/${id}/reset-password`, { method: "POST", body: { password } });

export const listAudit = (params: { limit?: number; offset?: number; action?: string }) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  return http<AuditPage>(`/audit?${q}`);
};

export type Overall = "OK" | "WARN" | "ERROR" | "OFFLINE";

export interface Viewer {
  id: "lichtblick_web" | "lichtblick" | "novnc" | "native";
  label: string;
  available: boolean;
  port: number | null;
  kind: "foxglove" | "http" | "note";
  /** may contain {host}: the host of foxglove_bridge (the browser's, or Workstation A's) */
  path: string;
  /** REMOTE: foxglove_bridge runs on Workstation A */
  ws_host: string | null;
  hint: string;
  /** Shell commands that start / enable this viewer (the native windows), to copy. */
  commands: string[];
}

export type HealthStatus = "ready" | "waiting" | "warn" | "error" | "off" | "unknown" | "down";

export interface HealthRow {
  id: string;
  status: HealthStatus;
  detail: string;
}

export interface ModeInfo {
  id: string;
  chip: string;
  robot: "mock" | "gazebo" | "real" | "remote" | "unknown";
  rviz: boolean;
  viewers: string;
  command: string;
  container: string;
  state: string;
  status: string;
}

export interface Status {
  ts: string;
  fake: boolean;
  overall: Overall;
  reasons: string[];
  host: { os: string; os_name: string; os_version: string; arch: string; hostname: string; label: string };
  docker: { reachable: boolean; error: string };
  mode: ModeInfo | null;
  transition: string | null;
  arm: string | null;
  ros_running: boolean;
  stop: { available: boolean; reason: string };
  ready: boolean;
  components: HealthRow[];
  viewers: Viewer[];
  jobs: { running: number; current: string | null; step: string | null; transition: string | null };
  infra: { id: string; name: string; service: string; state: string; status: string }[];
  banner: string;
  /** stack containers that exited abnormally (killed, crashed) and are still there */
  dead: { name: string; status: string; chip: string }[];
  /** REC (§12a.3): the running session and the ingest */
  rec: {
    session: string | null;
    recording: boolean;
    available: boolean;
    mode?: string;
    rate_hz?: number;
    samples?: number;
    ingest: { lag_s?: number; degraded?: boolean; connected?: boolean; written?: number; dropped?: number } & Record<
      string,
      unknown
    >;
  };
  alerts: AlertSummary;
}

export interface ModeRow {
  id: string;
  chip: string;
  robot: string;
  rviz: boolean;
  viewers: string;
  headless: boolean | null;
  command: string;
  service: string;
  startable: boolean;
  why_not: string;
  here: boolean;
  /** the exact command the cockpit runs to start it (detached) */
  start_command?: string;
  ar3_mode: string | null;
  platforms: string[];
}

export const getState = () => http<Status>("/env/state");
export const getModes = () => http<ModeRow[]>("/env/modes");

/** Where viewer links point: the host the browser used for the cockpit (§9.3), so they work
 * from another machine on the LAN; in REMOTE, foxglove_bridge is Workstation A's. */
export function viewerUrl(v: Viewer): string {
  const host = typeof window === "undefined" ? "localhost" : window.location.hostname;
  const bridge = v.ws_host ?? host;
  if (v.kind === "foxglove") return `ws://${bridge}:${v.port}`;
  if (v.kind === "http") return `http://${host}:${v.port}${v.path.replace("{host}", bridge)}`;
  return "";
}

// --- Environment (§9): start / stop, components, the REAL arm ---------------------------------

export const startEnv = (body: { mode: string; lichtblick?: boolean; switch?: boolean; confirm?: boolean }) =>
  http<Job>("/env/start", { method: "POST", body });
export const stopEnv = (confirm = false) => http<Job>("/env/stop", { method: "POST", body: { confirm } });
export const restartComponent = (id: string) => http<Job>(`/env/components/${id}/restart`, { method: "POST" });

export interface Component {
  id: string;
  name: string;
  what: string;
  runs_in: string[];
  ports: number[];
  depends: string[];
  health: string | null;
  layer: "browser" | "host" | "stack" | "infra";
  links: { to: string; label: string }[];
  cli: { start: string; stop: string; logs: string; status: string; shell: string };
  docs: string;
  in_mode: boolean;
  live: HealthRow | null;
}

export const getComponents = (mode?: string) =>
  http<{ mode: string | null; components: Component[] }>(`/components${mode ? `?mode=${mode}` : ""}`);

export type PreflightStatus = "pending" | "running" | "pass" | "fail" | "overridden";

export interface PreflightStep {
  id: "devices" | "estop" | "workspace" | "homing" | "pose" | "limits";
  title: string;
  description: string;
  status: PreflightStatus;
  detail: string;
  user?: string;
  at?: string;
}

export interface ArmState {
  real: boolean;
  container?: string;
  arm: string | null;
  estop?: boolean | null;
  agent_error?: string;
  estop_phase?: "" | "press" | "reset";
  steps: PreflightStep[];
  next?: string | null;
  can_arm?: boolean;
  override: boolean;
  limits?: {
    velocity_scaling: number;
    acceleration_scaling: number;
    table_size_m: [number, number];
    joint_limits_deg: Record<string, [number, number]>;
    source: string;
  };
}

export const getArmState = () => http<ArmState>("/arm/state");
export const preflight = (step: string, action = "run") =>
  http<ArmState>(`/arm/preflight/${step}`, { method: "POST", body: { action } });
export const disarm = () => http<ArmState>("/arm/disarm", { method: "POST" });
export const softStop = () =>
  http<{ cancelled: { action: string; goals: number }[]; held: string[]; errors: string[] }>("/arm/stop", {
    method: "POST",
  });
export const getJoints = () => http<{ name: string[]; position: number[] }>("/arm/joints");

// --- Install & Setup (§8): shapes mirror backend/setup/checks/base.py and setup/service.py ---

export type CheckStatus = "pass" | "warn" | "fail" | "skip" | "unknown" | "running";

export interface Check {
  id: string;
  title: string;
  group: string;
  status: CheckStatus;
  detail: string;
  fix: string;
  fix_commands: string[];
  fix_step: string;
  fix_params: Record<string, string>;
  cli: string[];
  docs: string;
  last_checked: string;
}

export interface Checklist {
  host: Status["host"];
  groups: string[];
  checks: Check[];
  summary: { done: number; total: number };
}

export interface Step {
  id: string;
  title: string;
  description: string;
  kind: "command" | "python" | "manual";
  command: string;
  params: { name: string; pattern: string; description: string }[];
  available: boolean;
  requires: string[];
  timeout_s: number;
  mutating: boolean;
  needs_root: boolean;
  instructions: string[];
  instructions_text: string;
  /** Official install guide for this host (empty when there is none). */
  doc_url: string;
  post_checks: string[];
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timeout";

export interface ErrorMatch {
  id: string;
  severity: "info" | "warn" | "error" | "critical";
  title: string;
  hint: string;
  docs: string;
  fix_step: string;
  seq: number;
  line: string;
}

export interface Job {
  id: string;
  step_id: string;
  title: string;
  params: Record<string, string>;
  command: string;
  user: string;
  status: JobStatus;
  exit_code: number | null;
  cancel_requested: boolean;
  matches: ErrorMatch[];
  lines: number;
  created: string;
  started: string | null;
  finished: string | null;
  duration_s: number | null;
  post_checks: string[];
}

export interface JobLine {
  seq: number;
  ts: string;
  stream: "out" | "err" | "cmd";
  text: string;
  match?: string;
}

export const getChecks = () => http<Checklist>("/checks");
export const runChecks = (ids?: string[]) => http<Checklist>("/checks/run", { method: "POST", body: { ids } });
export const getSteps = () => http<Step[]>("/steps");
export const startJob = (step_id: string, params?: Record<string, string>) =>
  http<Job>("/jobs", { method: "POST", body: { step_id, params } });
export const listJobs = (limit = 10) => http<Job[]>(`/jobs?limit=${limit}`);
export const getJob = (id: string) => http<Job>(`/jobs/${id}`);
export const cancelJob = (id: string) => http<Job>(`/jobs/${id}/cancel`, { method: "POST" });
export const continueSetup = () =>
  http<{ job: Job | null; check?: string; manual?: string[] }>("/setup/continue", { method: "POST" });
export const jobLogUrl = (id: string) => `/api/jobs/${id}/log`;
export const ACTIVE: JobStatus[] = ["queued", "running"];

/**
 * Monitoring and data capture (docs/specs/spec_cockpit.md §10, §12a). The shapes mirror
 * backend/monitoring/*.py and backend/telemetry/*.py; these endpoints return free-form objects
 * in the OpenAPI schema.
 */
import { http } from "./http";
import type { Job } from "./cockpit";

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- metrics (§10.1) -------------------------------------------------------------------------

export interface HostMetrics {
  cpu_percent: number;
  cpu_count: number;
  load_1m: number;
  mem_used_gb: number;
  mem_total_gb: number;
  mem_percent: number;
  disk_free_gb: number;
  disk_total_gb: number;
  disk_path: string;
  uptime_s: number;
}

export interface ContainerMetrics {
  name: string;
  cpu_percent: number;
  mem_used_mb: number;
  mem_limit_mb: number;
  mem_percent: number;
  pids: number;
}

export interface GpuMetrics {
  index: number;
  name: string;
  util_percent: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  temp_c: number | null;
}

export const getHostMetrics = () => http<HostMetrics>("/metrics/host");
export const getContainerMetrics = () =>
  http<{ containers: ContainerMetrics[]; gpus: GpuMetrics[]; ts: number | null }>("/metrics/containers");

// --- logs (§10.2) ----------------------------------------------------------------------------

export const LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"] as const;
export type Level = (typeof LEVELS)[number];

export interface LogLine {
  time: string;
  source: string;
  level: Level;
  node: string;
  text: string;
}

export interface LogFilter {
  source?: string;
  level?: string;
  node?: string;
  q?: string;
  regex?: boolean;
  from?: string;
  to?: string;
}

const logQuery = (f: LogFilter, extra: Record<string, string | number> = {}) =>
  query({ source: f.source, level: f.level, node: f.node, q: f.q, regex: f.regex ? 1 : undefined, from: f.from, to: f.to, ...extra });

export const getLogs = (f: LogFilter, limit = 500, before?: string) =>
  http<{ lines: LogLine[]; limit: number; more: boolean }>(`/logs${logQuery(f, { limit, ...(before ? { before } : {}) })}`);
export const getLogSources = () => http<{ source: string; lines: number; last: string | null }[]>("/logs/sources");
export const logsDownloadUrl = (f: LogFilter) => `/api/logs${logQuery(f, { download: 1 })}`;
export const logsSocketPath = (f: LogFilter) => `/ws/logs${logQuery(f)}`;

// --- ROS graph (§10.4) -------------------------------------------------------------------------

export interface GraphTopic {
  name: string;
  types: string[];
  publishers: string[];
  subscribers: string[];
  rate?: { hz: number; age_s: number | null };
}

export interface Graph {
  nodes: string[];
  topics: GraphTopic[];
  services: { name: string; types: string[] }[];
  actions: { name: string; types: string[] }[];
}

export const getGraph = () => http<Graph>("/ros/graph");
export const getNodeParams = (node: string) =>
  http<{ node: string; params: { name: string; value: unknown; unavailable?: boolean }[] }>(
    `/ros/node/${encodeURIComponent(node.replace(/^\//, ""))}/params`,
  );
export const measureRate = (name: string, seconds = 2) =>
  http<{ topic: string; hz: number; messages: number; window_s: number }>(`/ros/topic/rate${query({ name, seconds })}`);

// --- cameras (§10.3) ---------------------------------------------------------------------------

export const getCameras = () => http<{ cameras: string[] }>("/cameras");
export const cameraThumbUrl = (name: string, n: number) => `/api/cameras/${encodeURIComponent(name)}/thumb?n=${n}`;

// --- alerts (§10.5) ----------------------------------------------------------------------------

export type Severity = "info" | "warn" | "error" | "critical";

export interface Alert {
  id: number;
  rule: string;
  subject: string;
  severity: Severity;
  status: "active" | "acknowledged" | "resolved";
  message: string;
  details: Record<string, unknown>;
  first_seen: string;
  last_seen: string;
  count: number;
  acknowledged_by: string;
  acknowledged_at: string | null;
  comment: string;
  resolved_at: string | null;
}

export interface AlertSummary {
  active: number;
  acknowledged: number;
  by_severity: Record<Severity, number>;
  worst: Severity | null;
  recent: Alert[];
}

export interface AlertRule {
  id: string;
  title: string;
  when: string;
  severity: string;
  topic: string;
  subjects: string;
}

export const getAlerts = (status = "open", limit = 200) =>
  http<{ alerts: Alert[]; summary: AlertSummary }>(`/alerts${query({ status, limit })}`);
export const ackAlert = (id: number, comment = "") =>
  http<Alert>(`/alerts/${id}/ack`, { method: "POST", body: { comment } });
export const ackAlerts = (ids: number[], comment = "") =>
  http<{ acknowledged: Alert[] }>("/alerts", { method: "POST", body: { ids, comment } });
export const getAlertRules = () => http<AlertRule[]>("/alerts/rules");

// --- telemetry and data capture (§10.3, §12a) ------------------------------------------------

export interface Sample {
  t: number;
  p: (number | null)[];
  v: (number | null)[];
  e: (number | null)[];
  cp?: (number | null)[];
  cv?: (number | null)[];
  s?: { estop: boolean; homed: boolean; in_workspace: boolean; faults: string[] };
}

export interface TelemetryMessage {
  type: "telemetry";
  rate: number;
  names: string[];
  samples: Sample[];
  events: ({ kind: string; t: number } & Record<string, unknown>)[];
  rates?: Record<string, { hz: number; age_s: number | null }>;
  rtf?: number | null;
}

export interface Session {
  id: string;
  host: string;
  os: string;
  mode: "sim_mock" | "sim_gazebo" | "real" | "remote";
  mode_id: string;
  source: "sim" | "real";
  git_commit: string;
  config_hash: string;
  user: string;
  container: string;
  started: string;
  ended: string | null;
  recording: boolean;
  rate_hz: number;
  samples: number;
  dropped: number;
}

export interface SessionDetail extends Session {
  counts: Record<string, number> & {
    per_joint: { joint: string; samples: number; from: string; to: string; rate_hz: number | null }[];
  };
  recordings: Recording[];
  events: { time: string; kind: string; payload: Record<string, unknown> }[];
}

export interface Recording {
  id: string;
  session: string | null;
  kind: "mcap" | "lerobot";
  path: string;
  start: string;
  end: string | null;
  topics: { name: string; type: string; messages: number }[];
  size: number;
}

export interface JointHistory {
  session: string;
  from?: string;
  to?: string;
  bucket_s: number;
  names: string[];
  series: Record<string, { t: number[]; pos: number[]; pos_min: number[]; pos_max: number[]; vel: number[]; cmd: (number | null)[]; n: number[] }>;
}

export interface ExportItem {
  name: string;
  session: string;
  format: string;
  exported: string;
  files: { name: string; size: number }[];
}

export const listSessions = (limit = 50) => http<Session[]>(`/sessions${query({ limit })}`);
export const getSession = (id: string) => http<SessionDetail>(`/sessions/${id}`);
export const getLiveWindow = () => http<{ names: string[]; samples: Sample[] }>("/telemetry/live");
export const setRecording = (recording: boolean) =>
  http<Session>("/sessions/current/recording", { method: "POST", body: { recording } });
export const getJointHistory = (session: string, from?: string, to?: string, bucket?: number) =>
  http<JointHistory>(`/telemetry/joints${query({ session, from, to, bucket })}`);
export const getTrajectory = () =>
  http<{ trajectory: { time: string; names: string[]; points: { t: number; p: (number | null)[] }[] } | null }>(
    "/telemetry/trajectory",
  );
export const startExport = (body: { session: string; format: "parquet" | "csv"; from?: string; to?: string }) =>
  http<Job>("/telemetry/export", { method: "POST", body });
export const listExports = () => http<ExportItem[]>("/telemetry/export");
export const exportFileUrl = (name: string, file: string) =>
  `/api/telemetry/exports/${encodeURIComponent(name)}/${encodeURIComponent(file)}`;
export const listRecordings = () => http<Recording[]>("/recordings");

export const listJobsFiltered = (f: { status?: string; step?: string; user?: string; q?: string; limit?: number }) =>
  http<Job[]>(`/jobs${query({ limit: 100, ...f })}`);

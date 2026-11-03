import { Spinner } from "@heroui/react";
import type { CheckStatus, JobStatus } from "@/lib/api/cockpit";

const ICONS: Record<string, [string, string]> = {
  pass: ["bi-check-circle-fill", "text-success"],
  succeeded: ["bi-check-circle-fill", "text-success"],
  warn: ["bi-exclamation-triangle-fill", "text-warning"],
  fail: ["bi-x-circle-fill", "text-danger"],
  failed: ["bi-x-circle-fill", "text-danger"],
  timeout: ["bi-clock-fill", "text-danger"],
  cancelled: ["bi-slash-circle", "text-default-500"],
  skip: ["bi-dash-circle", "text-default-400"],
  unknown: ["bi-question-circle-fill", "text-default-500"],
  queued: ["bi-hourglass-split", "text-default-500"],
};

export function StatusIcon({ status, className = "" }: { status: CheckStatus | JobStatus; className?: string }) {
  if (status === "running") return <Spinner size="sm" className={className} />;
  const [icon, color] = ICONS[status] ?? ICONS.unknown;
  return <i className={`bi ${icon} ${color} ${className}`} aria-label={status} title={status} />;
}

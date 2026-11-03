import { Tooltip } from "@heroui/react";
import type { HealthStatus } from "@/lib/api/cockpit";

export const HEALTH_COLOR: Record<HealthStatus, string> = {
  ready: "bg-success",
  waiting: "bg-warning",
  warn: "bg-warning",
  error: "bg-danger",
  off: "bg-default-300",
  unknown: "bg-default-400",
  down: "bg-danger",
};

/** A status dot for a health row (docs/specs/spec_cockpit.md §9.2). */
export function HealthDot({ status, detail }: { status: HealthStatus; detail?: string }) {
  return (
    <Tooltip content={detail ? `${status}: ${detail}` : status}>
      <span
        className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${HEALTH_COLOR[status]} ${status === "waiting" ? "animate-pulse" : ""}`}
        data-status={status}
        aria-label={status}
      />
    </Tooltip>
  );
}

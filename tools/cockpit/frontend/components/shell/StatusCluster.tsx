"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { addToast, Button, Chip, Popover, PopoverContent, PopoverTrigger, Spinner, Tooltip } from "@heroui/react";
import { softStop, viewerUrl, type Overall, type Viewer } from "@/lib/api/cockpit";
import { setRecording } from "@/lib/api/monitoring";
import { useStatus } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";
import { CopyButton } from "@/components/common/CopyButton";

const PILL: Record<Overall, "success" | "warning" | "danger" | "default"> = {
  OK: "success",
  WARN: "warning",
  ERROR: "danger",
  OFFLINE: "default",
};

const ARM_COLOR: Record<string, "default" | "success" | "primary" | "danger" | "warning"> = {
  NOT_HOMED: "warning",
  HOMED: "primary",
  ARMED: "success",
  E_STOP: "danger",
  FAULT: "danger",
};

const VIEWER_ICONS: Record<Viewer["id"], string> = {
  lichtblick_web: "bi-box",
  lichtblick: "bi-pc-display",
  novnc: "bi-display",
  native: "bi-window-stack",
};

/** The always-visible status cluster of the header (docs/specs/spec_cockpit.md §7.2). */
export function StatusCluster() {
  const { status, connected } = useStatus();
  const { t } = useI18n();
  const stop = useMutation({
    mutationFn: softStop,
    onSuccess: (res) =>
      addToast({
        title: t("stop.done", { goals: res.cancelled.reduce((n, c) => n + c.goals, 0) }),
        color: "success",
      }),
    onError: (err: Error) => addToast({ title: t("stop.failed", { detail: err.message }), color: "danger" }),
  });
  const rec = useMutation({
    mutationFn: setRecording,
    onError: (err: Error) => addToast({ title: err.message, color: "danger" }),
  });
  if (!status) {
    return <Spinner size="sm" aria-label={t("common.loading")} />;
  }
  const overall = connected ? status.overall : "OFFLINE";
  const reasons = status.reasons.length ? status.reasons.join(" ") : t("status.pillHint");
  return (
    <div className="flex items-center gap-1.5" data-testid="status-cluster">
      <Tooltip content={reasons}>
        <Link href={status.alerts?.active ? "/monitoring/?tab=alerts" : "/monitoring/"} data-testid="status-pill">
          <Chip
            color={PILL[overall]}
            variant="solid"
            size="sm"
            className="font-semibold"
            endContent={status.alerts?.active ? <span className="ml-1 text-xs" data-testid="alert-count">{status.alerts.active}</span> : undefined}
          >
            {overall}
          </Chip>
        </Link>
      </Tooltip>
      <Tooltip content={status.mode ? `${status.mode.container}: ${status.mode.command}` : t("env.none")}>
        <Chip
          variant="flat"
          size="sm"
          color={status.mode ? "primary" : "default"}
          startContent={status.transition ? <Spinner size="sm" /> : undefined}
          data-testid="mode-chip"
        >
          {status.transition ?? status.mode?.chip ?? t("status.stopped")}
        </Chip>
      </Tooltip>
      <Chip variant="bordered" size="sm" data-testid="host-chip" className="hidden md:flex">
        {status.host.label}
      </Chip>
      {status.arm && (
        <Chip
          size="sm"
          variant="solid"
          color={ARM_COLOR[status.arm] ?? "default"}
          className={status.arm === "E_STOP" ? "animate-pulse" : ""}
          data-testid="arm-chip"
        >
          {status.arm.replace("_", " ")}
        </Chip>
      )}
      {status.rec?.available && (
        <Tooltip
          content={
            <div className="max-w-xs text-sm">
              <p className="font-semibold">{status.rec.recording ? t("rec.on", { hz: status.rec.rate_hz ?? 0 }) : t("rec.off")}</p>
              <p className="opacity-80">{t("rec.hint")}</p>
              {status.rec.ingest?.degraded && <p className="text-warning">{t("rec.degraded")}</p>}
            </div>
          }
        >
          <Chip
            as="button"
            size="sm"
            variant={status.rec.recording ? "solid" : "bordered"}
            color={status.rec.recording ? "danger" : "default"}
            startContent={<i className={`bi ${status.rec.recording ? "bi-record-circle-fill animate-pulse" : "bi-record-circle"}`} />}
            onClick={() => rec.mutate(!status.rec.recording)}
            data-testid="rec-toggle"
            data-recording={status.rec.recording}
            className="cursor-pointer"
          >
            REC
          </Chip>
        </Tooltip>
      )}
      {status.viewers.map((v) => (
        <ViewerButton key={v.id} viewer={v} />
      ))}
      {status.jobs.running > 0 && (
        <Tooltip content={t("job.indicator", { n: status.jobs.running, step: status.jobs.step ?? "" })}>
          <Link href={`/setup/job/?id=${status.jobs.current}`} data-testid="job-indicator">
            <Chip size="sm" variant="flat" startContent={<Spinner size="sm" />}>
              {status.jobs.running}
            </Chip>
          </Link>
        </Tooltip>
      )}
      {status.ros_running && (
        <Tooltip
          content={
            <div className="max-w-xs text-sm">
              <p className="font-semibold">{t("status.stopHint")}</p>
              {!status.stop.available && <p className="mt-1 opacity-80">{status.stop.reason}</p>}
            </div>
          }
        >
          {/* span: a disabled button gets no hover events, the tooltip needs them */}
          <span>
            <Button
              size="sm"
              color="danger"
              variant="solid"
              isDisabled={!status.stop.available}
              isLoading={stop.isPending}
              onPress={() => stop.mutate()}
              startContent={<i className="bi bi-stop-circle" />}
              data-testid="stop-button"
            >
              {t("status.stop")}
            </Button>
          </span>
        </Tooltip>
      )}
    </div>
  );
}

function ViewerButton({ viewer }: { viewer: Viewer }) {
  const { t } = useI18n();
  const icon = <i className={`bi ${VIEWER_ICONS[viewer.id]} text-lg`} />;
  if (!viewer.available) {
    return (
      <Tooltip content={viewer.kind === "note" ? viewer.hint : t("status.notRunning", { name: viewer.label })}>
        <span>
          <Button isIconOnly size="sm" variant="light" isDisabled aria-label={viewer.label} data-testid={`viewer-${viewer.id}`}>
            {icon}
          </Button>
        </span>
      </Tooltip>
    );
  }
  const url = viewerUrl(viewer);
  if (viewer.kind === "http") {
    return (
      <Tooltip content={`${viewer.label}: ${viewer.hint}`}>
        <Button
          as="a"
          href={url}
          target="_blank"
          rel="noopener"
          isIconOnly
          size="sm"
          variant="light"
          color="primary"
          aria-label={viewer.label}
          data-testid={`viewer-${viewer.id}`}
        >
          {icon}
        </Button>
      </Tooltip>
    );
  }
  // Lichtblick desktop: the app connects to the WebSocket URL.
  return (
    <Popover placement="bottom">
      <PopoverTrigger>
        <Button isIconOnly size="sm" variant="light" color="primary" aria-label={viewer.label} data-testid={`viewer-${viewer.id}`}>
          {icon}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="max-w-sm p-3">
        <p className="mb-1 text-sm font-semibold">{viewer.label}</p>
        <p className="mb-2 text-sm">{viewer.hint}</p>
        <div className="flex items-center gap-2">
          <code className="cli rounded bg-default-100 px-2 py-1">{url}</code>
          <CopyButton text={url} />
        </div>
      </PopoverContent>
    </Popover>
  );
}

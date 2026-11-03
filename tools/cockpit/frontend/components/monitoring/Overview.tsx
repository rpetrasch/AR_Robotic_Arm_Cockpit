"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, CardBody, CardHeader, Progress } from "@heroui/react";
import type { Status } from "@/lib/api/cockpit";
import { getContainerMetrics, getHostMetrics } from "@/lib/api/monitoring";
import { useI18n } from "@/lib/i18n";
import { HealthDot } from "@/components/environment/HealthDot";

function uptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

function Tile({ label, value, sub, testId }: { label: string; value: string; sub?: string; testId?: string }) {
  return (
    <Card shadow="sm" data-testid={testId}>
      <CardBody className="gap-1">
        <span className="text-xs uppercase opacity-70">{label}</span>
        <span className="text-2xl font-semibold">{value}</span>
        {sub && <span className="text-xs opacity-70">{sub}</span>}
      </CardBody>
    </Card>
  );
}

/** Monitoring → Overview (docs/specs/spec_cockpit.md §10.1). */
export function Overview({ status }: { status: Status }) {
  const { t } = useI18n();
  const host = useQuery({ queryKey: ["metrics", "host"], queryFn: getHostMetrics, refetchInterval: 2000 });
  const cont = useQuery({ queryKey: ["metrics", "containers"], queryFn: getContainerMetrics, refetchInterval: 5000 });
  const h = host.data;
  const a = status.alerts;
  const rtf = status.components.find((c) => c.id === "gazebo");
  const containerUptime = status.mode?.status ?? "";
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label={t("mon.mode")} value={status.mode?.chip ?? t("status.stopped")} sub={containerUptime} testId="tile-mode" />
        <Tile
          label={t("mon.alerts")}
          value={String(a?.active ?? 0)}
          sub={a ? t("mon.alertsBy", { c: a.by_severity.critical, e: a.by_severity.error, w: a.by_severity.warn, ack: a.acknowledged }) : ""}
          testId="tile-alerts"
        />
        <Tile label={t("mon.hostUptime")} value={h ? uptime(h.uptime_s) : "…"} sub={status.host.label} />
        <Tile label={t("mon.rtf")} value={rtf && rtf.status !== "off" ? rtf.detail : "—"} sub={t("mon.rtfHint")} testId="tile-rtf" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card data-testid="host-metrics">
          <CardHeader className="font-semibold">{t("mon.host")}</CardHeader>
          <CardBody className="gap-3 pt-0">
            {h && (
              <>
                <Progress size="sm" label={t("mon.cpu", { n: h.cpu_count, load: h.load_1m })} value={h.cpu_percent} showValueLabel aria-label="CPU" />
                <Progress
                  size="sm"
                  label={t("mon.mem", { used: h.mem_used_gb.toFixed(1), total: h.mem_total_gb.toFixed(1) })}
                  value={h.mem_percent}
                  showValueLabel
                  aria-label="memory"
                />
                <Progress
                  size="sm"
                  color={h.disk_free_gb < 15 ? "danger" : "primary"}
                  label={t("mon.disk", { free: h.disk_free_gb.toFixed(0), total: h.disk_total_gb.toFixed(0) })}
                  value={100 - (100 * h.disk_free_gb) / h.disk_total_gb}
                  showValueLabel
                  aria-label="disk"
                />
              </>
            )}
            {(cont.data?.gpus ?? []).map((g) => (
              <div key={g.index} className="text-sm" data-testid="gpu">
                <Progress size="sm" label={`${g.name} · ${g.temp_c ?? "?"} °C`} value={g.util_percent ?? 0} showValueLabel aria-label="GPU" />
                <span className="text-xs opacity-70">{t("mon.gpuMem", { used: g.mem_used_mb ?? "?", total: g.mem_total_mb ?? "?" })}</span>
              </div>
            ))}
            {status.host.os === "macos" && <p className="text-xs opacity-70">{t("mon.noGpuMac")}</p>}
          </CardBody>
        </Card>
        <Card data-testid="container-metrics">
          <CardHeader className="font-semibold">{t("mon.containers")}</CardHeader>
          <CardBody className="pt-0">
            <table className="w-full text-sm">
              <thead className="text-left opacity-70">
                <tr>
                  <th className="py-1 pr-3 font-medium">{t("mon.container")}</th>
                  <th className="py-1 pr-3 text-right font-medium">CPU</th>
                  <th className="py-1 text-right font-medium">{t("mon.memory")}</th>
                </tr>
              </thead>
              <tbody>
                {(cont.data?.containers ?? []).map((c) => (
                  <tr key={c.name} className="border-t border-default-200">
                    <td className="py-1 pr-3 font-mono">{c.name}</td>
                    <td className="py-1 pr-3 text-right">{c.cpu_percent.toFixed(1)} %</td>
                    <td className="py-1 text-right">{c.mem_used_mb.toFixed(0)} MB</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs opacity-70">{t("mon.containersHint")}</p>
          </CardBody>
        </Card>
      </div>
      <Card data-testid="overview-health">
        <CardHeader className="font-semibold">{t("env.health")}</CardHeader>
        <CardBody className="grid gap-x-6 gap-y-1 pt-0 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {status.components.map((c) => (
            <span key={c.id} className="flex items-center gap-2" title={c.detail}>
              <HealthDot status={c.status} detail={c.detail} />
              <span className="font-mono">{c.id}</span>
              <span className="truncate opacity-70">{c.status}</span>
            </span>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}

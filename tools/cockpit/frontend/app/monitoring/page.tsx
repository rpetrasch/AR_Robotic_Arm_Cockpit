"use client";

import { useEffect, useState } from "react";
import { Chip, Spinner, Tab, Tabs } from "@heroui/react";
import { useStatus } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";
import { Alerts } from "@/components/monitoring/Alerts";
import { Jobs } from "@/components/monitoring/Jobs";
import { Logs } from "@/components/monitoring/Logs";
import { Overview } from "@/components/monitoring/Overview";
import { RosGraph } from "@/components/monitoring/RosGraph";
import { Telemetry } from "@/components/monitoring/Telemetry";

const TABS = ["overview", "logs", "telemetry", "graph", "alerts", "jobs"] as const;

/**
 * Monitoring (docs/specs/spec_cockpit.md §10): Overview · Logs · Telemetry · ROS Graph · Alerts ·
 * Jobs. The tab is in the URL (?tab=logs), so the header pill can link to the alerts.
 */
export default function MonitoringPage() {
  const { t } = useI18n();
  const { status } = useStatus();
  const [tab, setTab] = useState<string>("overview");
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("tab");
    if (q && (TABS as readonly string[]).includes(q)) setTab(q);
  }, []);
  const pick = (k: string) => {
    setTab(k);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", k);
    window.history.replaceState(null, "", url);
  };
  if (!status) return <Spinner label={t("common.loading")} />;
  const active = status.alerts?.active ?? 0;
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">{t("nav.monitoring")}</h1>
      <Tabs aria-label={t("nav.monitoring")} selectedKey={tab} onSelectionChange={(k) => pick(String(k))} data-testid="monitoring-tabs">
        <Tab key="overview" title={t("mon.tabOverview")}>
          <Overview status={status} />
        </Tab>
        <Tab key="logs" title={t("mon.tabLogs")} data-testid="tab-logs">
          <Logs />
        </Tab>
        <Tab key="telemetry" title={t("mon.tabTelemetry")} data-testid="tab-telemetry">
          <Telemetry status={status} />
        </Tab>
        <Tab key="graph" title={t("mon.tabGraph")} data-testid="tab-graph">
          <RosGraph running={status.ros_running} />
        </Tab>
        <Tab
          key="alerts"
          data-testid="tab-alerts"
          title={
            <span className="flex items-center gap-2">
              {t("mon.tabAlerts")}
              {active > 0 && (
                <Chip size="sm" color="danger" variant="solid">
                  {active}
                </Chip>
              )}
            </span>
          }
        >
          <Alerts />
        </Tab>
        <Tab key="jobs" title={t("mon.tabJobs")} data-testid="tab-jobs">
          <Jobs />
        </Tab>
      </Tabs>
    </div>
  );
}

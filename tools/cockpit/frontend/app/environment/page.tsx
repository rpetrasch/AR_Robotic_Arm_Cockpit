"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Chip, Spinner, Tab, Tabs } from "@heroui/react";
import { getModes, restartComponent, startEnv, stopEnv, type Job } from "@/lib/api/cockpit";
import { ApiError } from "@/lib/api/http";
import { useStatus } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";
import { Components } from "@/components/environment/Components";
import { CurrentStack } from "@/components/environment/CurrentStack";
import { EnvJob } from "@/components/environment/EnvJob";
import { StartForm, type StartRequest } from "@/components/environment/StartForm";
import { Viewers } from "@/components/environment/Viewers";

/**
 * Environment (docs/specs/spec_cockpit.md §9): the running stack (from Docker, also when started
 * from the CLI), start / stop / stop-and-switch as jobs with the exact command, component
 * health and catalogue, viewers. REAL gets the pre-flight wizard (/environment/preflight/).
 */
export default function EnvironmentPage() {
  const { t } = useI18n();
  const { status } = useStatus();
  const modes = useQuery({ queryKey: ["env", "modes"], queryFn: getModes, staleTime: Infinity });
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<{ text: string; jobId?: string } | null>(null);

  const [tab, setTab] = useState("start");
  const chosen = useRef(false); // the user picked a tab: do not jump to the log on their own

  const onJob = (job: Job) => {
    setError(null);
    setJobId(job.id);
    setTab("log"); // the log gets the whole screen while a job runs
  };
  const onError = (err: Error) => {
    if (err instanceof ApiError && err.status === 409 && err.problem.job_id) {
      setError({ text: err.message, jobId: String(err.problem.job_id) });
    } else {
      setError({ text: err.message });
    }
  };
  const start = useMutation({ mutationFn: (req: StartRequest) => startEnv(req), onSuccess: onJob, onError });
  const stop = useMutation({ mutationFn: (confirm: boolean) => stopEnv(confirm), onSuccess: onJob, onError });
  const restart = useMutation({ mutationFn: (id: string) => restartComponent(id), onSuccess: onJob, onError });

  const hasEnvJob = !!(status?.jobs.step?.startsWith("env.") || status?.jobs.step === "viewers.lichtblick") && !!status?.jobs.current;
  useEffect(() => {
    if (hasEnvJob && !chosen.current) setTab("log"); // a job that was already running on arrival
  }, [hasEnvJob]);

  // On the log tab the page is exactly one screen tall (below the top bar and any banner), so the
  // log scrolls and the page does not.
  const ready = !!status && !!modes.data;
  const logTab = tab === "log" && (!!jobId || hasEnvJob);
  const page = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (!ready || !logTab || !page.current) {
      setFit(null);
      return;
    }
    const measure = () => {
      const top = page.current!.getBoundingClientRect().top + window.scrollY;
      setFit(Math.max(420, window.innerHeight - top - 24)); // 24 = the page's bottom padding
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [ready, logTab]);

  if (!status || !modes.data) return <Spinner label={t("common.loading")} />;
  const os = status.host.os;
  const here = modes.data.filter((m) => m.here && m.robot !== "unknown");
  const envJob = status.jobs.step?.startsWith("env.") || status.jobs.step === "viewers.lichtblick" ? status.jobs.current : null;
  const shownJob = jobId ?? envJob;
  const busy = status.jobs.running > 0 || start.isPending || stop.isPending;
  const real = modes.data.find((m) => m.id === "real");
  const lichtblickOff = status.components.find((c) => c.id === "lichtblick_web")?.status === "off";

  const onLog = tab === "log" && !!shownJob;
  return (
    <div ref={page} className="flex flex-col gap-4" style={onLog && fit ? { height: fit } : undefined}>
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{t("nav.environment")}</h1>
        <Chip variant="bordered" size="sm">
          {status.host.label}
        </Chip>
      </div>

      <CurrentStack status={status} busy={busy} onStop={(c) => stop.mutate(c)} onRestart={() => restart.mutate("ros")} compact={onLog} />
      {error && (
        <p className="text-sm text-danger" role="alert" data-testid="env-error">
          {error.text}{" "}
          {error.jobId && (
            <Button size="sm" variant="light" onPress={() => setJobId(error.jobId!)}>
              {t("setup.openJob")}
            </Button>
          )}
        </p>
      )}
      <Tabs
        aria-label={t("nav.environment")}
        data-testid="env-tabs"
        selectedKey={onLog ? "log" : tab === "log" ? "start" : tab}
        onSelectionChange={(k) => {
          chosen.current = true;
          setTab(String(k));
        }}
        classNames={onLog ? { base: "shrink-0", tabWrapper: "flex min-h-0 flex-1 flex-col", panel: "flex min-h-0 flex-1 flex-col overflow-hidden" } : undefined}
      >
        {shownJob ? (
          <Tab key="log" title={t("env.tabLog")} data-testid="tab-log">
            <EnvJob id={shownJob} fill={onLog} />
          </Tab>
        ) : null}
        <Tab key="start" title={t("env.tabStart")}>
          <div className="flex flex-col gap-4">
            <StartForm status={status} modes={here} busy={busy} onStart={(req) => start.mutate(req)} />
            {os === "linux" && real && (
              <Card data-testid="real-card">
                <CardHeader className="font-semibold">{t("env.realTitle")}</CardHeader>
                <CardBody className="flex flex-col gap-2 pt-0">
                  <p className="text-sm">{t("env.realHint")}</p>
                  <p className="text-sm opacity-80">{real.why_not}</p>
                  <CliLine command={real.command} />
                </CardBody>
              </Card>
            )}
            <Card data-testid="env-matrix">
              <CardHeader className="font-semibold">{t("env.command")}</CardHeader>
              <CardBody className="pt-0">
                <table className="w-full text-sm">
                  <thead className="text-left opacity-70">
                    <tr>
                      <th className="py-1 pr-3 font-medium">{t("env.robot")}</th>
                      <th className="py-1 pr-3 font-medium">{t("env.viewers")}</th>
                      <th className="py-1 font-medium">{t("env.command")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {here
                      .filter((m) => m.robot !== "real")
                      .map((m) => (
                        <tr key={m.id} className="border-t border-default-200" data-testid={`mode-${m.id}`}>
                          <td className="py-1 pr-3">{m.robot === "gazebo" ? "Gazebo" : m.robot}</td>
                          <td className="py-1 pr-3">{m.viewers === "Lichtblick" || m.robot === "remote" ? m.viewers : `${m.viewers} + Lichtblick`}</td>
                          <td className="min-w-72 py-1">
                            <CliLine command={m.command} />
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {os === "linux" && <p className="mt-3 text-sm opacity-80">{t("env.linuxWindows")}</p>}
              </CardBody>
            </Card>
          </div>
        </Tab>
        <Tab key="components" title={t("env.tabComponents")} data-testid="tab-components">
          <Components rows={status.components} modes={here} current={status.mode?.id ?? null} />
        </Tab>
        <Tab key="viewers" title={t("env.tabViewers")} data-testid="tab-viewers">
          <Viewers viewers={status.viewers} lichtblickOff={lichtblickOff} busy={busy} onStartLichtblick={() => restart.mutate("lichtblick")} />
        </Tab>
      </Tabs>
    </div>
  );
}

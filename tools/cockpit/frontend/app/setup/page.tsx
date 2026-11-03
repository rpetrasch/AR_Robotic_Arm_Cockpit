"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Chip, Progress, Spinner } from "@heroui/react";
import {
  continueSetup,
  getChecks,
  getSteps,
  listJobs,
  runChecks,
  startJob,
  type Check,
  type Checklist,
  type Step,
} from "@/lib/api/cockpit";
import { ApiError } from "@/lib/api/http";
import { useSocket } from "@/lib/api/socket";
import { useI18n } from "@/lib/i18n";
import { CheckRow } from "@/components/setup/CheckRow";
import { StatusIcon } from "@/components/setup/StatusIcon";

const FOCUS_KEY = "ar3cockpit.setup.focus";

/** The check whose fix was started: the page scrolls back to it after the job (per tab). */
function rememberCheck(id: string) {
  try {
    sessionStorage.setItem(FOCUS_KEY, id);
  } catch {
    // private window: the page then opens at the top
  }
}

/** Install & Setup (docs/specs/spec_cockpit.md §8): the checklist, "Continue setup", recent jobs. */
export default function SetupPage() {
  const { t } = useI18n();
  const router = useRouter();
  const qc = useQueryClient();
  const checks = useQuery({ queryKey: ["checks"], queryFn: getChecks, staleTime: 0, refetchOnMount: "always" });
  const steps = useQuery({ queryKey: ["steps"], queryFn: getSteps, staleTime: Infinity });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => listJobs(6), staleTime: 0, refetchOnMount: "always" });
  const [rechecking, setRechecking] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<{ text: string; jobId?: string } | null>(null);

  // A finished job re-runs its checks on the server; /ws/checks says which.
  useSocket("/ws/checks", () => {
    qc.invalidateQueries({ queryKey: ["checks"] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
  });

  const setList = (data: Checklist) => qc.setQueryData(["checks"], data);
  const recheck = useMutation({
    mutationFn: (ids?: string[]) => runChecks(ids),
    onMutate: (ids) => setRechecking(new Set(ids ?? checks.data?.checks.map((c) => c.id) ?? [])),
    onSuccess: setList,
    onSettled: () => setRechecking(new Set()),
  });
  const onJobError = (err: Error) => {
    if (err instanceof ApiError && err.status === 409) {
      setMessage({ text: t("setup.busy"), jobId: String(err.problem.job_id ?? "") });
    } else {
      setMessage({ text: err.message });
    }
  };
  const run = useMutation({
    mutationFn: ({ step, params }: { step: Step; params: Record<string, string> }) =>
      startJob(step.id, Object.keys(params ?? {}).length ? params : undefined),
    onSuccess: (job) => router.push(`/setup/job/?id=${job.id}`),
    onError: onJobError,
  });
  const cont = useMutation({
    mutationFn: continueSetup,
    onSuccess: (res) => {
      if (res.job) router.push(`/setup/job/?id=${res.job.id}`);
      else
        setMessage({
          text: res.manual?.length
            ? `${t("setup.allDone")} ${t("setup.manualLeft", { items: res.manual.join(", ") })}`
            : t("setup.allDone"),
        });
    },
    onError: onJobError,
  });

  const ready = !!checks.data && !!steps.data;
  useEffect(() => {
    if (!ready) return;
    try {
      const id = sessionStorage.getItem(FOCUS_KEY);
      if (!id) return;
      sessionStorage.removeItem(FOCUS_KEY);
      document.querySelector(`[data-testid="check-${CSS.escape(id)}"]`)?.scrollIntoView({ block: "center" });
    } catch {
      // storage blocked: stay at the top
    }
  }, [ready]);

  if (!checks.data || !steps.data) {
    return <Spinner label={t("common.loading")} />;
  }
  const list = checks.data;
  const stepById = new Map(steps.data.map((s) => [s.id, s]));
  const status = new Map(list.checks.map((c) => [c.id, c.status]));
  const blockedBy = (step?: Step) =>
    (step?.requires ?? []).filter((id) => status.get(id) !== "pass");
  const { done, total } = list.summary;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{t("setup.title")}</h1>
        <Chip variant="bordered" size="sm">{list.host.label}</Chip>
      </div>

      <Card data-testid="setup-summary">
        <CardBody className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-lg font-semibold" data-testid="setup-done">
              {t("setup.done", { done, total })}
            </span>
            <Progress aria-label={t("setup.done", { done, total })} value={total ? (100 * done) / total : 0} color="success" className="max-w-md flex-1" />
            <Button color="primary" startContent={<i className="bi bi-play-fill" />} isLoading={cont.isPending} onPress={() => cont.mutate()} data-testid="continue-setup">
              {t("setup.continue")}
            </Button>
            <Button variant="flat" startContent={<i className="bi bi-arrow-clockwise" />} isLoading={recheck.isPending && rechecking.size > 1} onPress={() => recheck.mutate(undefined)} data-testid="recheck-all">
              {t("setup.recheckAll")}
            </Button>
          </div>
          <p className="text-sm opacity-70">{t("setup.continueHint")}</p>
          {message && (
            <p className="text-sm" role="status" data-testid="setup-message">
              {message.text}{" "}
              {message.jobId && (
                <Link className="text-primary underline" href={`/setup/job/?id=${message.jobId}`}>
                  {t("setup.openJob")}
                </Link>
              )}
            </p>
          )}
        </CardBody>
      </Card>

      {list.groups.map((group) => {
        const items = list.checks.filter((c) => c.group === group);
        if (!items.length) return null;
        return (
          <Card key={group} data-testid={`group-${group}`}>
            <CardHeader className="flex items-center gap-2 pb-0 font-semibold">
              {group}
              <GroupBadge items={items} />
            </CardHeader>
            <CardBody className="divide-y divide-default-200 pt-0">
              {items.map((c) => {
                const step = c.fix_step ? stepById.get(c.fix_step) : undefined;
                return (
                  <CheckRow
                    key={c.id}
                    check={c}
                    step={step}
                    blockedBy={blockedBy(step)}
                    rechecking={rechecking.has(c.id)}
                    starting={run.isPending && run.variables?.step.id === step?.id}
                    onRecheck={() => recheck.mutate([c.id])}
                    onRun={(s, params) => {
                      rememberCheck(c.id);
                      run.mutate({ step: s, params });
                    }}
                  />
                );
              })}
            </CardBody>
          </Card>
        );
      })}

      <Card data-testid="recent-jobs">
        <CardHeader className="font-semibold">{t("setup.recentJobs")}</CardHeader>
        <CardBody className="flex flex-col gap-1 pt-0">
          {(jobs.data ?? []).length === 0 && <p className="text-sm opacity-70">{t("setup.noJobs")}</p>}
          {(jobs.data ?? []).map((j) => (
            <Link key={j.id} href={`/setup/job/?id=${j.id}`} className="flex items-center gap-2 rounded px-1 py-1 text-sm hover:bg-default-100">
              <StatusIcon status={j.status} />
              <span className="font-medium">{j.title}</span>
              <code className="cli opacity-70">{j.command}</code>
              <span className="ml-auto opacity-60">{new Date(j.created).toLocaleString()}</span>
            </Link>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}

function GroupBadge({ items }: { items: Check[] }) {
  const worst = items.some((c) => c.status === "fail")
    ? "fail"
    : items.some((c) => c.status === "warn" || c.status === "unknown")
      ? "warn"
      : "pass";
  return <StatusIcon status={worst} />;
}

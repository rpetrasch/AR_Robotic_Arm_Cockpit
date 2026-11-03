"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, CardBody, Chip, Spinner } from "@heroui/react";
import {
  ACTIVE,
  cancelJob,
  getSteps,
  jobLogUrl,
  startJob,
  type ErrorMatch,
  type Job,
  type JobLine,
} from "@/lib/api/cockpit";
import { ApiError } from "@/lib/api/http";
import { useSocket } from "@/lib/api/socket";
import { useI18n } from "@/lib/i18n";
import { useAssistant, usePageContext } from "@/lib/assistant";
import { CliLine } from "@/components/common/CopyButton";
import { JobLog, levelOf } from "@/components/setup/JobLog";
import { StatusIcon } from "@/components/setup/StatusIcon";

const COLOR = { succeeded: "success", failed: "danger", timeout: "danger", cancelled: "default", running: "primary", queued: "default" } as const;

export default function JobPage() {
  return (
    <Suspense>
      <JobView />
    </Suspense>
  );
}

/** One job (docs/specs/spec_cockpit.md §8.3): live over /ws/jobs/<id>; static export, so the id is
 * a query parameter (/setup/job/?id=…). */
function JobView() {
  const { t } = useI18n();
  const router = useRouter();
  const id = useSearchParams().get("id");
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<JobLine[]>([]);
  const [missing, setMissing] = useState(false);
  const [focus, setFocus] = useState<number | null>(null);
  const [levelsOnly, setLevelsOnly] = useState(false);
  const steps = useQuery({ queryKey: ["steps"], queryFn: getSteps, staleTime: Infinity });
  const assistant = useAssistant();
  usePageContext(id ? { job_id: id } : {});

  useSocket(
    id ? `/ws/jobs/${id}` : null,
    (msg) => {
      if (msg.type === "job") setJob(msg.job);
      if (msg.type === "lines") {
        setLines((prev) => {
          const last = prev.length ? prev[prev.length - 1].seq : 0;
          const fresh = (msg.lines as JobLine[]).filter((l) => l.seq > last);
          return fresh.length ? [...prev, ...fresh] : prev;
        });
      }
    },
    () => setLines([]), // a reconnect resends the whole log
  );
  // The socket closes with 4404 for an unknown job; show that instead of a spinner.
  useQuery({
    queryKey: ["job-exists", id],
    queryFn: async () => {
      const res = await fetch(`/api/jobs/${id}`, { credentials: "same-origin" });
      if (res.status === 404) setMissing(true);
      return res.status;
    },
    enabled: !!id,
  });

  const cancel = useMutation({ mutationFn: () => cancelJob(id!), onSuccess: setJob });
  const fix = useMutation({
    mutationFn: (stepId: string) => startJob(stepId),
    onSuccess: (j) => router.push(`/setup/job/?id=${j.id}`),
  });

  if (!id || missing) return <p>{t("job.notFound")}</p>;
  if (!job) return <Spinner label={t("common.loading")} />;
  const active = ACTIVE.includes(job.status);
  const stepById = new Map((steps.data ?? []).map((s) => [s.id, s]));
  const levelCount = (kind: "error" | "warn") => lines.filter((l) => levelOf(l.text) === kind).length;
  const errorLines = levelCount("error");
  const warnLines = levelCount("warn");
  const showLevels = () => {
    setLevelsOnly((on) => !on);
    document.querySelector('[data-testid="job-log"]')?.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  const ok = job.status === "succeeded"; // the exit code decides; matches only explain
  const distinct = [...new Map(job.matches.map((m) => [m.id, m])).values()];

  return (
    <div className="flex flex-col gap-4" data-testid="job-page" data-status={job.status}>
      <Link href="/setup/" className="text-sm text-primary">
        <i className="bi bi-arrow-left" /> {t("job.back")}
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <StatusIcon status={job.status} className="text-2xl" />
        <h1 className="text-2xl font-semibold">{job.title}</h1>
        <Chip color={COLOR[job.status]} variant="flat" data-testid="job-status">
          {job.status}
        </Chip>
        {job.exit_code !== null && <span className="text-sm opacity-70">{t("job.exit", { code: job.exit_code })}</span>}
        {job.duration_s !== null && <span className="text-sm opacity-70">{job.duration_s.toFixed(1)} s</span>}
        <span className="text-sm opacity-70">{t("job.by", { user: job.user })}</span>
        <div className="flex-1" />
        {active && (
          <Button color="danger" variant="flat" onPress={() => cancel.mutate()} isLoading={cancel.isPending} isDisabled={job.cancel_requested} data-testid="job-cancel">
            {t("job.cancel")}
          </Button>
        )}
      </div>
      <CliLine command={job.command} />

      {distinct.length > 0 && (
        <Card className={`border-2 ${ok ? "border-warning-200" : "border-danger-200"}`} data-testid="error-panel">
          <CardBody className="flex flex-col gap-3">
            <h2 className={`font-semibold ${ok ? "text-warning" : "text-danger"}`}>{t(ok ? "job.errorsOk" : "job.errors")}</h2>
            {distinct.map((m) => (
              <MatchItem
                key={m.id}
                match={m}
                fixTitle={m.fix_step ? stepById.get(m.fix_step)?.title : undefined}
                fixManual={m.fix_step ? stepById.get(m.fix_step)?.kind === "manual" : false}
                onShow={() => setFocus(m.seq)}
                onFix={() => fix.mutate(m.fix_step)}
                onAsk={() =>
                  assistant.ask({
                    question: t("job.askQuestion", { title: m.title, step: job.title }),
                    task: "chat",
                    context: { job_id: job.id, match_id: m.id, line: m.line },
                  })
                }
              />
            ))}
            {fix.error && (
              <p className="text-sm text-danger">
                {fix.error instanceof ApiError && fix.error.status === 409 ? t("setup.busy") : fix.error.message}
              </p>
            )}
          </CardBody>
        </Card>
      )}

      {!active && job.post_checks.length > 0 && (
        <p className="text-sm opacity-70">
          {t("job.rechecked", { items: job.post_checks.join(", ") })}{" "}
          <Link className="text-primary underline" href="/setup/">
            {t("job.back")}
          </Link>
        </p>
      )}

      {(errorLines + warnLines > 0 || distinct.length > 0) && (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="level-bar">
          <span className="opacity-80">{t("job.levelCounts", { errors: errorLines, warnings: warnLines })}</span>
          <Button size="sm" variant="flat" onPress={showLevels} data-testid="show-levels">
            {levelsOnly ? t("job.showWholeLog") : t("job.showLevels")}
          </Button>
          <Button
            size="sm"
            variant="flat"
            color="secondary"
            startContent={<i className="bi bi-stars" />}
            isDisabled={active}
            onPress={() => assistant.ask({ question: t("job.explainQuestion", { step: job.title }), task: "error_explain", context: { job_id: job.id } })}
            data-testid="explain-ai"
          >
            {t("job.explainAI")}
          </Button>
        </div>
      )}

      <JobLog
        lines={lines}
        downloadUrl={jobLogUrl(job.id)}
        focusSeq={focus}
        levelsOnly={levelsOnly}
        onLevelsOnly={setLevelsOnly}
        onAskLine={(l) => assistant.ask({ question: t("job.askLineQuestion", { step: job.title }), task: "chat", context: { job_id: job.id, line: l.text } })}
      />
    </div>
  );
}

function MatchItem({
  match,
  fixTitle,
  fixManual,
  onShow,
  onFix,
  onAsk,
}: {
  match: ErrorMatch;
  fixTitle?: string;
  fixManual: boolean;
  onShow: () => void;
  onFix: () => void;
  onAsk: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-1" data-testid={`match-${match.id}`}>
      <div className="flex items-center gap-2">
        <Chip size="sm" color={match.severity === "warn" ? "warning" : "danger"} variant="flat">
          {match.severity}
        </Chip>
        <span className="font-medium">{match.title}</span>
      </div>
      <p className="text-sm" data-testid={`hint-${match.id}`}>{match.hint}</p>
      <button type="button" className="cli truncate text-left text-xs opacity-70 hover:underline" onClick={onShow}>
        {match.line}
      </button>
      <div className="flex flex-wrap gap-2">
        {fixTitle && !fixManual && (
          <Button size="sm" color="primary" variant="flat" onPress={onFix}>
            {t("job.runFix", { step: fixTitle })}
          </Button>
        )}
        {fixTitle && fixManual && (
          <Link href="/setup/" className="text-sm text-primary underline">
            {fixTitle}
          </Link>
        )}
        <Button size="sm" variant="light" color="secondary" startContent={<i className="bi bi-stars" />} onPress={onAsk} data-testid={`ask-${match.id}`}>
          {t("job.askAssistant")}
        </Button>
        {match.docs && <span className="self-center text-xs opacity-60">{match.docs}</span>}
      </div>
    </div>
  );
}

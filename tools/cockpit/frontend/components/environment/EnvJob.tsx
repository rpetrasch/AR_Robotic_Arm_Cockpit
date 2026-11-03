"use client";

import Link from "next/link";
import { Card, CardBody, CardHeader, Chip, Spinner } from "@heroui/react";
import { ACTIVE, jobLogUrl } from "@/lib/api/cockpit";
import { useJobStream } from "@/lib/api/jobStream";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";
import { JobLog } from "@/components/setup/JobLog";

const COLOR = { succeeded: "success", failed: "danger", timeout: "danger", cancelled: "default", running: "primary", queued: "default" } as const;

/** The start / stop job of this page, live (the full page is /setup/job/?id=…). */
export function EnvJob({ id, fill }: { id: string; fill?: boolean }) {
  const { t } = useI18n();
  const { job, lines } = useJobStream(id);
  if (!job) return null;
  const active = ACTIVE.includes(job.status);
  const match = job.matches[0];
  return (
    <Card data-testid="env-job" className={fill ? "min-h-0 flex-1" : ""}>
      <CardHeader className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{job.title}</span>
        <Chip size="sm" color={COLOR[job.status]} variant="flat" startContent={active ? <Spinner size="sm" /> : undefined} data-testid="env-job-status">
          {job.status}
        </Chip>
        <Link href={`/setup/job/?id=${job.id}`} className="ml-auto text-sm text-primary underline">
          {t("env.openJob")}
        </Link>
      </CardHeader>
      <CardBody className={`flex flex-col gap-2 pt-0 ${fill ? "min-h-0 flex-1 overflow-hidden" : ""}`}>
        <CliLine command={job.command} />
        {match && !active && job.status !== "succeeded" && (
          <p className="text-sm text-danger" data-testid="env-job-hint">
            <b>{match.title}.</b> {match.hint}
          </p>
        )}
        <div className={fill ? "min-h-0 flex-1" : "h-56"}>
          <JobLog lines={lines} downloadUrl={jobLogUrl(job.id)} fill={fill} />
        </div>
      </CardBody>
    </Card>
  );
}

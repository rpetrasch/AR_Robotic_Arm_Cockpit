"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, CardBody, Chip, Input, Select, SelectItem } from "@heroui/react";
import { startJob, type Job } from "@/lib/api/cockpit";
import { ApiError } from "@/lib/api/http";
import { listJobsFiltered } from "@/lib/api/monitoring";
import { useI18n } from "@/lib/i18n";

const COLOR = { succeeded: "success", failed: "danger", timeout: "danger", cancelled: "default", running: "primary", queued: "default" } as const;
const STATUSES = ["", "running,queued", "succeeded", "failed,timeout", "cancelled"];

/** Monitoring → Jobs (docs/specs/spec_cockpit.md §10.6): filter, status, duration, user, log, re-run. */
export function Jobs() {
  const { t } = useI18n();
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [q, setQ] = useState("");
  const jobs = useQuery({
    queryKey: ["jobs-all", status, user, q],
    queryFn: () => listJobsFiltered({ status: status || undefined, user: user || undefined, q: q || undefined, limit: 200 }),
    refetchInterval: 3000,
  });
  const [msg, setMsg] = useState<string | null>(null);
  const rerun = useMutation({
    mutationFn: (j: Job) => startJob(j.step_id, j.params),
    onSuccess: (j) => setMsg(t("jobs.started", { id: j.id.slice(0, 8) })),
    onError: (e: Error) => setMsg(e instanceof ApiError ? e.message : String(e)),
  });
  return (
    <div className="flex flex-col gap-3">
      <Card>
        <CardBody className="flex flex-row flex-wrap items-end gap-3">
          <Select label={t("jobs.status")} size="sm" className="w-44" selectedKeys={[status || "__all"]} onSelectionChange={(k) => {
            const v = Array.from(k)[0] as string;
            setStatus(v === "__all" ? "" : v);
          }}>
            {STATUSES.map((s) => (
              <SelectItem key={s || "__all"}>{s || t("jobs.any")}</SelectItem>
            ))}
          </Select>
          <Input label={t("jobs.user")} size="sm" className="w-40" value={user} onValueChange={setUser} />
          <Input label={t("jobs.command")} size="sm" className="w-72" value={q} onValueChange={setQ} data-testid="jobs-search" />
        </CardBody>
      </Card>
      {msg && <p className="text-sm" role="status">{msg}</p>}
      <table className="w-full text-sm" data-testid="jobs-table">
        <thead className="text-left opacity-70">
          <tr>
            <th className="py-1 pr-3 font-medium">{t("jobs.when")}</th>
            <th className="py-1 pr-3 font-medium">{t("jobs.command")}</th>
            <th className="py-1 pr-3 font-medium">{t("jobs.status")}</th>
            <th className="py-1 pr-3 text-right font-medium">{t("jobs.duration")}</th>
            <th className="py-1 pr-3 font-medium">{t("jobs.user")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(jobs.data ?? []).map((j) => (
            <tr key={j.id} className="border-t border-default-200" data-testid="job-row">
              <td className="py-1 pr-3 whitespace-nowrap">{new Date(j.created).toLocaleString()}</td>
              <td className="py-1 pr-3 font-mono text-xs">
                <Link href={`/setup/job/?id=${j.id}`} className="text-primary underline">
                  {j.command}
                </Link>
              </td>
              <td className="py-1 pr-3">
                <Chip size="sm" variant="flat" color={COLOR[j.status]}>
                  {j.status}
                  {j.exit_code !== null && j.status !== "succeeded" ? ` (${j.exit_code})` : ""}
                </Chip>
              </td>
              <td className="py-1 pr-3 text-right">{j.duration_s != null ? `${j.duration_s.toFixed(1)} s` : "—"}</td>
              <td className="py-1 pr-3">{j.user}</td>
              <td className="py-1 text-right">
                <Button size="sm" variant="light" isDisabled={j.status === "running" || j.status === "queued"} onPress={() => rerun.mutate(j)} data-testid="rerun">
                  <i className="bi bi-arrow-repeat" /> {t("jobs.rerun")}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

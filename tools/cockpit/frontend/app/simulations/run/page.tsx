"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AlignedData } from "uplot";
import { Button, Card, CardBody, CardHeader, Checkbox, Chip, Input, Select, SelectItem, Spinner, Switch } from "@heroui/react";
import { abortRun, compareRuns, getScenario, listRuns, pauseRun, startRun, type RunRecord, type RunStep } from "@/lib/api/scenarios";
import type { Sample, TelemetryMessage } from "@/lib/api/monitoring";
import { ApiError } from "@/lib/api/http";
import { useSocket } from "@/lib/api/socket";
import { useStatus } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";
import { EnvJob } from "@/components/environment/EnvJob";
import { Chart } from "@/components/monitoring/Chart";
import { Robot3D } from "@/components/monitoring/Robot3D";
import { RESULT_COLOR } from "@/components/scenarios/colors";

const STEP_COLOR: Record<string, "default" | "primary" | "success" | "danger" | "warning"> = {
  pending: "default",
  running: "primary",
  succeeded: "success",
  failed: "danger",
  aborted: "warning",
};
const ROBOT_MODE: Record<string, string> = { mock: "sim_mock", gazebo: "sim_gazebo", real: "real" };

function secs(v?: number | null): string {
  return v == null ? "—" : `${v.toFixed(2)} s`;
}

/** Run panel, run history and compare (docs/specs/spec_cockpit.md §12.3). `?id=<scenario>&run=<id>`. */
export default function RunPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const { status } = useStatus();
  const [id, setId] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setId(q.get("id"));
    setFocus(q.get("run"));
  }, []);
  const scenario = useQuery({ queryKey: ["scenario", id], queryFn: () => getScenario(id!), enabled: !!id });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => listRuns(id!), enabled: !!id, refetchInterval: 2000 });
  const [job, setJob] = useState<string | null>(null);
  const [mode, setMode] = useState("");
  const [repeat, setRepeat] = useState("1");
  const [seed, setSeed] = useState("");
  const [keep, setKeep] = useState(false);
  const [bag, setBag] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [cmp, setCmp] = useState<string[]>([]);

  const running = status?.mode ? ROBOT_MODE[status.mode.robot] : undefined;
  const modes = ((scenario.data?.data?.modes as string[]) ?? []).filter((m) => !running || m === running);
  const start = useMutation({
    mutationFn: () => startRun(id!, { mode: mode || undefined, repeat: Number(repeat) || 1, seed: seed === "" ? null : Number(seed), keep_scene: keep, record: bag }),
    onSuccess: (j) => {
      setErr(null);
      setJob(j.id);
      setFocus(null);
      qc.invalidateQueries({ queryKey: ["runs", id] });
    },
    onError: (e: Error) => setErr(e instanceof ApiError ? `${e.problem.title}: ${e.message}` : String(e)),
  });
  // the run being shown: the newest of the job just started, else ?run=, else the newest
  const shown = useMemo(() => {
    const list = runs.data ?? [];
    if (job) return list.find((r) => r.job_id === job) ?? null;
    if (focus) return list.find((r) => r.id === focus) ?? null;
    return list[0] ?? null;
  }, [runs.data, job, focus]);

  if (!id) return <Spinner />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/simulations/" className="text-sm text-primary underline">
          ← {t("nav.simulations")}
        </Link>
        <h1 className="text-2xl font-semibold">{(scenario.data?.data?.name as string) ?? id}</h1>
        <Link href={`/simulations/edit/?id=${id}`} className="text-sm text-primary underline">
          {t("sim.edit")}
        </Link>
      </div>
      {scenario.data && scenario.data.errors.length > 0 && (
        <p className="text-sm text-danger" data-testid="run-invalid">
          {scenario.data.errors.map((p) => p.message).join("; ")}
        </p>
      )}
      <Card data-testid="run-form">
        <CardBody className="flex flex-row flex-wrap items-end gap-3">
          <Select size="sm" className="w-44" label={t("sim.mode")} selectedKeys={[mode || modes[0] || "__none"]} onSelectionChange={(k) => setMode(Array.from(k)[0] as string)}
            description={running ? t("sim.runningMode", { mode: running }) : t("sim.noStack")}>
            {(modes.length ? modes : ["__none"]).map((m) => <SelectItem key={m}>{m === "__none" ? "—" : m}</SelectItem>)}
          </Select>
          <Input size="sm" className="w-24" type="number" min={1} max={100} label={t("sim.repeat")} value={repeat} onValueChange={setRepeat} />
          <Input size="sm" className="w-32" type="number" label={t("sim.seed")} value={seed} onValueChange={setSeed} />
          <Switch size="sm" isSelected={keep} onValueChange={setKeep}>
            {t("sim.keepScene")}
          </Switch>
          <Switch size="sm" isSelected={bag} onValueChange={setBag}>
            {t("sim.recordBag")}
          </Switch>
          <Button color="primary" isDisabled={!modes.length || !!scenario.data?.errors.length || start.isPending || shown?.status === "running"} onPress={() => start.mutate()} data-testid="run-start">
            <i className="bi bi-play-fill" /> {t("sim.run")}
          </Button>
        </CardBody>
      </Card>
      {err && (
        <p className="text-sm text-danger" role="alert" data-testid="run-error">
          {err}
        </p>
      )}
      {shown && <LiveRun run={shown} />}
      {job && <EnvJob id={job} />}
      <History runs={runs.data ?? []} cmp={cmp} setCmp={setCmp} onShow={(r) => (setJob(null), setFocus(r))} />
      {cmp.length === 2 && <Compare a={cmp[0]} b={cmp[1]} />}
    </div>
  );
}

function LiveRun({ run }: { run: RunRecord }) {
  const { t } = useI18n();
  const [state, setState] = useState<RunRecord>(run);
  const [paused, setPaused] = useState(false);
  const [plan, setPlan] = useState<Record<string, number>[]>([]);
  const [names, setNames] = useState<string[]>([]);
  const buf = useRef<Sample[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  useEffect(() => setState(run), [run]);
  useSocket(`/ws/runs/${run.id}`, (msg) => {
    // the stored run (on connect, at the end) or, while running, its steps
    if (msg.state?.repeat) setState(msg.state as RunRecord);
    else if (msg.state?.steps) setState((s) => ({ ...s, steps: msg.state.steps, status: "running" }));
  });
  const live = state.status === "running";
  useSocket(live ? "/ws/telemetry" : null, (msg) => {
    if (msg.type !== "telemetry") return;
    const m = msg.data as TelemetryMessage;
    if (m.names?.length) setNames(m.names);
    buf.current = [...buf.current, ...m.samples].slice(-300);
    setSamples(buf.current);
    for (const ev of m.events) {
      if (ev.kind === "trajectory") {
        const tr = ev as unknown as { names: string[]; points: { p: number[] }[] };
        setPlan(tr.points.map((p) => Object.fromEntries(tr.names.map((n, i) => [n, p.p[i] ?? 0]))));
      }
    }
  });
  const pause = useMutation({ mutationFn: (on: boolean) => pauseRun(run.id, on), onSuccess: (r) => setPaused(r.paused) });
  const abort = useMutation({ mutationFn: () => abortRun(run.id) });
  const arm = names.map((n, i) => [n, i] as const).filter(([n]) => !n.includes("gripper"));
  const data = useMemo<AlignedData>(() => [samples.map((s) => s.t), ...arm.map(([, i]) => samples.map((s) => s.p[i] ?? null))] as AlignedData, [samples, names]); // eslint-disable-line react-hooks/exhaustive-deps
  const latest = samples.at(-1);
  const joints = Object.fromEntries(names.map((n, i) => [n, latest?.p[i] ?? 0]));
  const steps: RunStep[] = state.steps ?? [];

  return (
    <Card data-testid="live-run" data-status={state.status}>
      <CardHeader className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{t("sim.runTitle", { id: run.id.slice(0, 8), k: run.repeat[0], n: run.repeat[1] })}</span>
        <Chip size="sm" color={RESULT_COLOR[state.result || state.status] ?? "default"} variant="solid" data-testid="run-status">
          {state.result || state.status}
        </Chip>
        <span className="text-xs opacity-70">
          {state.mode} · {state.user} · v{state.version}
          {state.seed != null ? ` · seed ${state.seed}` : ""}
        </span>
        {live && (
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="flat" onPress={() => pause.mutate(!paused)} data-testid="run-pause">
              <i className={`bi ${paused ? "bi-play" : "bi-pause"}`} /> {paused ? t("sim.resume") : t("sim.pause")}
            </Button>
            <Button size="sm" color="danger" onPress={() => abort.mutate()} data-testid="run-abort">
              <i className="bi bi-stop-fill" /> {t("sim.abort")}
            </Button>
          </div>
        )}
      </CardHeader>
      <CardBody className="grid gap-4 pt-0 xl:grid-cols-2">
        <div className="flex flex-col gap-2">
          <table className="w-full text-sm" data-testid="run-steps">
            <tbody>
              {steps.map((s) => (
                <tr key={s.index} className="border-t border-default-200" data-testid={`run-step-${s.index}`} data-status={s.status}>
                  <td className="py-1 pr-2 font-mono">{s.index + 1}</td>
                  <td className="py-1 pr-2">{s.label}</td>
                  <td className="py-1 pr-2">
                    <Chip size="sm" variant="flat" color={STEP_COLOR[s.status]}>
                      {s.status}
                    </Chip>
                  </td>
                  <td className="py-1 text-right text-xs opacity-80">
                    {s.planning_s != null ? `${secs(s.planning_s)} + ${secs(s.execution_s)}` : ""}
                    {s.error ? <span className="text-danger"> {s.error}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {state.evaluation?.length > 0 && (
            <div className="flex flex-col gap-1 text-sm" data-testid="run-evaluation">
              <span className="font-medium">{t("sim.criteria")}</span>
              {state.evaluation.map((c) => (
                <span key={c.criterion} className={c.status === "fail" ? "text-danger" : c.status === "skipped" ? "opacity-70" : "text-success"}>
                  <i className={`bi ${c.status === "pass" ? "bi-check-circle" : c.status === "fail" ? "bi-x-circle" : "bi-dash-circle"}`} /> {c.criterion}: {c.detail}
                </span>
              ))}
            </div>
          )}
          {live && samples.length > 1 && <Chart data={data} series={arm.map(([n, i]) => ({ label: n.replace("joint_", "J"), slot: i }))} unit="rad" height={150} />}
        </div>
        {live ? <Robot3D joints={joints} plan={plan} /> : <p className="text-sm opacity-70">{t("sim.runDone", { d: secs(state.duration_s) })}</p>}
      </CardBody>
    </Card>
  );
}

function History({ runs, cmp, setCmp, onShow }: { runs: RunRecord[]; cmp: string[]; setCmp: (v: string[]) => void; onShow: (id: string) => void }) {
  const { t } = useI18n();
  return (
    <Card>
      <CardHeader className="flex items-center gap-2 font-semibold">
        {t("sim.history", { n: runs.length })}
        <span className="text-xs font-normal opacity-70">{t("sim.compareHint")}</span>
      </CardHeader>
      <CardBody className="pt-0">
        {runs.length === 0 ? (
          <p className="text-sm opacity-70">{t("sim.noRuns")}</p>
        ) : (
          <table className="w-full text-sm" data-testid="run-history">
            <thead className="text-left opacity-70">
              <tr>
                <th />
                <th className="py-1 pr-3 font-medium">{t("sim.runId")}</th>
                <th className="py-1 pr-3 font-medium">v</th>
                <th className="py-1 pr-3 font-medium">{t("sim.mode")}</th>
                <th className="py-1 pr-3 font-medium">{t("jobs.user")}</th>
                <th className="py-1 pr-3 font-medium">{t("jobs.when")}</th>
                <th className="py-1 pr-3 text-right font-medium">{t("jobs.duration")}</th>
                <th className="py-1 pr-3 font-medium">{t("sim.result")}</th>
                <th className="py-1 font-medium">{t("sim.failedStep")}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-default-200" data-testid="history-row">
                  <td className="py-1 pr-2">
                    <Checkbox size="sm" aria-label={r.id} isSelected={cmp.includes(r.id)}
                      onValueChange={(on) => setCmp(on ? [...cmp, r.id].slice(-2) : cmp.filter((x) => x !== r.id))} data-testid="compare-pick" />
                  </td>
                  <td className="py-1 pr-3 font-mono">
                    <button className="text-primary underline" onClick={() => onShow(r.id)}>
                      {r.id.slice(0, 8)}
                    </button>
                  </td>
                  <td className="py-1 pr-3">{r.version}</td>
                  <td className="py-1 pr-3">{r.mode}</td>
                  <td className="py-1 pr-3">{r.user}</td>
                  <td className="py-1 pr-3 whitespace-nowrap">{new Date(r.started).toLocaleString()}</td>
                  <td className="py-1 pr-3 text-right">{secs(r.duration_s)}</td>
                  <td className="py-1 pr-3">
                    <Chip size="sm" variant="flat" color={RESULT_COLOR[r.result || r.status] ?? "default"}>
                      {r.result || r.status}
                    </Chip>
                  </td>
                  <td className="py-1 text-xs">{r.failed_step != null ? `${r.failed_step + 1}: ${r.error}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardBody>
    </Card>
  );
}

function Compare({ a, b }: { a: string; b: string }) {
  const { t } = useI18n();
  const q = useQuery({ queryKey: ["compare", a, b], queryFn: () => compareRuns(a, b) });
  if (!q.data) return <Spinner />;
  const { a: ra, b: rb, steps } = q.data;
  const head = (r: RunRecord) => `${r.id.slice(0, 8)} · v${r.version} · ${r.mode} · ${r.result}`;
  return (
    <Card data-testid="compare">
      <CardHeader className="font-semibold">{t("sim.compare")}</CardHeader>
      <CardBody className="pt-0">
        <table className="w-full text-sm">
          <thead className="text-left opacity-70">
            <tr>
              <th className="py-1 pr-3 font-medium">{t("sim.step")}</th>
              <th className="py-1 pr-3 font-medium">{head(ra)}</th>
              <th className="py-1 font-medium">{head(rb)}</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((s) => (
              <tr key={s.index} className="border-t border-default-200">
                <td className="py-1 pr-3">
                  {s.index + 1}. {s.label}
                </td>
                {[s.a, s.b].map((x, i) => (
                  <td key={i} className="py-1 pr-3">
                    <Chip size="sm" variant="flat" color={STEP_COLOR[x.status ?? "pending"]}>
                      {x.status ?? "—"}
                    </Chip>{" "}
                    {x.planning_s != null ? `${secs(x.planning_s)} + ${secs(x.execution_s)}` : ""}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="border-t border-default-300 font-medium">
              <td className="py-1 pr-3">{t("sim.total")}</td>
              {[ra, rb].map((r) => (
                <td key={r.id} className="py-1 pr-3">
                  {secs(r.duration_s)} ({t("sim.planExec", { p: secs(r.metrics.planning_s), e: secs(r.metrics.execution_s) })})
                </td>
              ))}
            </tr>
            <tr className="border-t border-default-200">
              <td className="py-1 pr-3">{t("sim.criteria")}</td>
              {[ra, rb].map((r) => (
                <td key={r.id} className="py-1 pr-3 text-xs">
                  {r.evaluation.map((c) => `${c.criterion}: ${c.status}`).join(", ")}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { AlignedData } from "uplot";
import { Button, Card, CardBody, CardHeader, Chip, Select, SelectItem, Switch, Tab, Tabs } from "@heroui/react";
import {
  cameraThumbUrl,
  exportFileUrl,
  getCameras,
  getJointHistory,
  getSession,
  getTrajectory,
  listExports,
  listRecordings,
  listSessions,
  setRecording,
  startExport,
  type Sample,
  type TelemetryMessage,
} from "@/lib/api/monitoring";
import { startJob, type Status } from "@/lib/api/cockpit";
import { useSocket } from "@/lib/api/socket";
import { useI18n } from "@/lib/i18n";
import { EnvJob } from "@/components/environment/EnvJob";
import { Chart, type Series } from "./Chart";
import { Robot3D, type DetectedObject } from "./Robot3D";

const WINDOW_S = 60;

function short(name: string): string {
  return name.replace("joint_", "J").replace("gripper_jaw1_joint", "gripper");
}

/** Columns of the live window for one field of the samples (p, v, cp). */
function columns(samples: Sample[], names: string[], field: "p" | "v" | "cp", pick: number[]): AlignedData {
  const t = samples.map((s) => s.t);
  return [t, ...pick.map((i) => samples.map((s) => (s[field]?.[i] ?? null) as number | null))] as AlignedData;
}

function csv(samples: Sample[], names: string[]): string {
  const head = ["time", ...names.flatMap((n) => [`${n}.position`, `${n}.velocity`, `${n}.cmd_position`])];
  const rows = samples.map((s) =>
    [new Date(s.t * 1000).toISOString(), ...names.flatMap((_n, i) => [s.p[i] ?? "", s.v[i] ?? "", s.cp?.[i] ?? ""])].join(","),
  );
  return [head.join(","), ...rows].join("\n") + "\n";
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Monitoring → Telemetry (docs/specs/spec_cockpit.md §10.3, §12a.4). */
export function Telemetry({ status }: { status: Status }) {
  const { t } = useI18n();
  return (
    <Tabs aria-label={t("tel.title")} size="sm" variant="underlined" data-testid="telemetry-tabs">
      <Tab key="live" title={t("tel.live")}>
        <Live status={status} />
      </Tab>
      <Tab key="history" title={t("tel.history")} data-testid="tab-history">
        <History status={status} />
      </Tab>
    </Tabs>
  );
}

function Live({ status }: { status: Status }) {
  const { t } = useI18n();
  const buf = useRef<Sample[]>([]);
  const [names, setNames] = useState<string[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [rates, setRates] = useState<TelemetryMessage["rates"]>({});
  const [rtf, setRtf] = useState<number | null>(null);
  const [frozen, setFrozen] = useState(false);
  const [joint, setJoint] = useState(0);
  const [plan, setPlan] = useState<Record<string, number>[]>([]);
  const [objects, setObjects] = useState<DetectedObject[]>([]);
  const liveTimes = useRef<number[]>([]); // stamps received live: the rate shown
  const frozenRef = useRef(frozen);
  frozenRef.current = frozen;

  const trim = () => {
    const last = buf.current.at(-1)?.t ?? 0;
    const cut = buf.current.findIndex((s) => s.t >= last - WINDOW_S);
    if (cut > 0) buf.current = buf.current.slice(cut);
  };
  const toPlan = (traj: { names: string[]; points: { p: (number | null)[] }[] }) =>
    traj.points.map((pt) => Object.fromEntries(traj.names.map((n, i) => [n, pt.p[i] ?? 0])));

  useSocket(status.ros_running ? "/ws/telemetry" : null, (msg) => {
    if (msg.type === "window") {
      buf.current = msg.data.samples ?? [];
      if (msg.data.names?.length) setNames(msg.data.names);
    } else if (msg.type === "telemetry") {
      const m = msg.data as TelemetryMessage;
      if (m.names?.length) setNames((old) => (old.join() === m.names.join() ? old : m.names));
      const last = buf.current.at(-1)?.t ?? 0;
      const fresh = m.samples.filter((x) => x.t > last); // the cached window may overlap
      buf.current.push(...fresh);
      liveTimes.current.push(...fresh.map((x) => x.t));
      liveTimes.current = liveTimes.current.slice(-101);
      trim();
      if (m.rates) setRates(m.rates);
      if (m.rtf !== undefined) setRtf(m.rtf ?? null);
      for (const ev of m.events) {
        if (ev.kind === "trajectory") setPlan(toPlan(ev as never));
        if (ev.kind === "detections") setObjects((ev.objects as DetectedObject[]) ?? []);
      }
    }
    if (!frozenRef.current) setSamples([...buf.current]);
  });

  useEffect(() => {
    getTrajectory()
      .then((r) => r.trajectory && setPlan(toPlan(r.trajectory)))
      .catch(() => undefined);
  }, []);

  const arm = names.map((n, i) => [n, i] as const).filter(([n]) => !n.includes("gripper"));
  const gripper = names.findIndex((n) => n.includes("gripper"));
  const armIdx = arm.map(([, i]) => i);
  const armSeries: Series[] = arm.map(([n, i]) => ({ label: short(n), slot: i }));
  const pos = useMemo(() => columns(samples, names, "p", armIdx), [samples, names]); // eslint-disable-line react-hooks/exhaustive-deps
  const vel = useMemo(() => columns(samples, names, "v", armIdx), [samples, names]); // eslint-disable-line react-hooks/exhaustive-deps
  const cmd = useMemo<AlignedData>(() => {
    const ts = samples.map((s) => s.t);
    return [ts, samples.map((s) => s.p[joint] ?? null), samples.map((s) => s.cp?.[joint] ?? null)] as AlignedData;
  }, [samples, joint]);
  const grip = useMemo<AlignedData>(
    () => [samples.map((s) => s.t), samples.map((s) => (gripper >= 0 ? (s.p[gripper] ?? null) : null))] as AlignedData,
    [samples, gripper],
  );
  const latest = samples.at(-1);
  const current = Object.fromEntries(names.map((n, i) => [n, latest?.p[i] ?? 0]));
  const safety = samples.filter((s) => s.s);
  const lt = liveTimes.current;
  const rate = lt.length > 1 ? (lt.length - 1) / (lt[lt.length - 1] - lt[0]) : 0;

  if (!status.ros_running) return <p className="text-sm opacity-80">{t("tel.noStack")}</p>;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Chip size="sm" variant="flat" data-testid="live-rate">
          {t("tel.liveRate", { hz: rate.toFixed(1), n: samples.length })}
        </Chip>
        {rtf != null && (
          <Chip size="sm" variant="flat" data-testid="rtf">
            RTF {rtf.toFixed(2)}
          </Chip>
        )}
        <Switch size="sm" isSelected={frozen} onValueChange={setFrozen} data-testid="freeze">
          {t("tel.freeze")}
        </Switch>
        <Button size="sm" variant="flat" onPress={() => download("ar3-telemetry-window.csv", csv(samples, names))} data-testid="export-window">
          <i className="bi bi-download" /> {t("tel.exportWindow")}
        </Button>
        <span className="text-xs opacity-70">{t("tel.liveHint")}</span>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="text-sm font-semibold">{t("tel.positions")}</CardHeader>
          <CardBody className="pt-0">
            <Chart data={pos} series={armSeries} unit="rad" testId="chart-positions" />
          </CardBody>
        </Card>
        <Card>
          <CardHeader className="text-sm font-semibold">{t("tel.velocities")}</CardHeader>
          <CardBody className="pt-0">
            <Chart data={vel} series={armSeries} unit="rad/s" testId="chart-velocities" />
          </CardBody>
        </Card>
        <Card>
          <CardHeader className="flex items-center gap-3 text-sm font-semibold">
            {t("tel.cmdVsActual")}
            <Select
              aria-label={t("tel.joint")}
              size="sm"
              className="max-w-36"
              selectedKeys={[String(joint)]}
              onSelectionChange={(k) => setJoint(Number(Array.from(k)[0] ?? 0))}
            >
              {arm.map(([n, i]) => (
                <SelectItem key={String(i)}>{n}</SelectItem>
              ))}
            </Select>
          </CardHeader>
          <CardBody className="pt-0">
            <Chart
              data={cmd}
              series={[
                { label: t("tel.actual"), slot: joint },
                { label: t("tel.commanded"), slot: joint, dashed: true },
              ]}
              unit="rad"
              testId="chart-cmd"
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader className="text-sm font-semibold">{t("tel.gripper")}</CardHeader>
          <CardBody className="pt-0">
            {gripper >= 0 ? (
              <Chart data={grip} series={[{ label: short(names[gripper]), slot: gripper }]} unit="m" height={140} />
            ) : (
              <p className="text-sm opacity-70">{t("tel.noGripper")}</p>
            )}
            <p className="mt-2 text-xs opacity-70">{t("tel.gripperCurrent")}</p>
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader className="text-sm font-semibold">{t("tel.view3d")}</CardHeader>
          <CardBody className="pt-0">
            <Robot3D joints={current} plan={plan} objects={objects} />
            <p className="mt-2 text-xs opacity-70">
              {plan.length ? t("tel.planShown", { n: plan.length }) : t("tel.noPlan")} {objects.length ? t("tel.objects", { n: objects.length }) : ""}
            </p>
          </CardBody>
        </Card>
        <div className="flex flex-col gap-4">
          <Card data-testid="topic-rates">
            <CardHeader className="text-sm font-semibold">{t("tel.rates")}</CardHeader>
            <CardBody className="pt-0">
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(rates ?? {}).map(([topic, r]) => (
                    <tr key={topic} className="border-t border-default-200">
                      <td className="py-1 pr-3 font-mono">{topic}</td>
                      <td className="py-1 pr-3 text-right">{r.hz.toFixed(1)} Hz</td>
                      <td className="py-1 text-right opacity-70">{r.age_s == null ? t("tel.never") : `${r.age_s.toFixed(2)} s`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardBody>
          </Card>
          <Card>
            <CardHeader className="text-sm font-semibold">{t("tel.safety")}</CardHeader>
            <CardBody className="pt-0">
              {safety.length ? (
                <div className="flex h-6 w-full overflow-hidden rounded" data-testid="safety-timeline">
                  {safety.map((s) => (
                    <span
                      key={s.t}
                      className={`h-full flex-1 ${s.s!.estop ? "bg-danger" : "bg-success"}`}
                      title={`${new Date(s.t * 1000).toLocaleTimeString()}: ${s.s!.estop ? "E-STOP" : "ok"}`}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-sm opacity-70">{t("tel.noSafety")}</p>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
      <Cameras />
    </div>
  );
}

function Cameras() {
  const { t } = useI18n();
  const cams = useQuery({ queryKey: ["cameras"], queryFn: getCameras, retry: false });
  const [tick, setTick] = useState(0);
  const [on, setOn] = useState(true);
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => setTick((n) => n + 1), 500); // 2 fps; the agent makes ≤ 5
    return () => clearInterval(id);
  }, [on]);
  const list = cams.data?.cameras ?? [];
  return (
    <Card data-testid="cameras">
      <CardHeader className="flex items-center gap-3 text-sm font-semibold">
        {t("tel.cameras")}
        <Switch size="sm" isSelected={on} onValueChange={setOn}>
          {t("tel.camerasLive")}
        </Switch>
      </CardHeader>
      <CardBody className="pt-0">
        {list.length === 0 ? (
          <p className="text-sm opacity-70">{t("tel.noCameras")}</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {list.map((c) => (
              <figure key={c} className="flex flex-col gap-1">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={cameraThumbUrl(c, tick)} alt={c} className="w-full rounded bg-default-100" data-testid={`camera-${c}`} />
                <figcaption className="font-mono text-xs opacity-80">{c}</figcaption>
              </figure>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs opacity-70">{t("tel.overlaysPending")}</p>
      </CardBody>
    </Card>
  );
}

function History({ status }: { status: Status }) {
  const { t } = useI18n();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => listSessions(50), refetchInterval: 5000 });
  const [picked, setPicked] = useState<string | null>(null);
  const id = picked ?? sessions.data?.[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["session", id], queryFn: () => getSession(id!), enabled: !!id, refetchInterval: 5000 });
  const history = useQuery({ queryKey: ["history", id], queryFn: () => getJointHistory(id!), enabled: !!id });
  const exports = useQuery({ queryKey: ["exports"], queryFn: listExports, refetchInterval: 5000 });
  const recs = useQuery({ queryKey: ["recordings"], queryFn: listRecordings, refetchInterval: 5000 });
  const [job, setJob] = useState<string | null>(null);
  const exp = useMutation({ mutationFn: startExport, onSuccess: (j) => setJob(j.id) });
  const bag = useMutation({ mutationFn: () => startJob("rosbag.record", { preset: "default", duration: "60" }), onSuccess: (j) => setJob(j.id) });
  const rec = useMutation({ mutationFn: setRecording, onSuccess: () => sessions.refetch() });

  const data = useMemo<{ d: AlignedData; s: Series[] }>(() => {
    const h = history.data;
    if (!h || !h.names.length) return { d: [[]], s: [] };
    const first = h.series[h.names[0]];
    return {
      d: [first.t, ...h.names.map((n) => h.series[n].pos)] as AlignedData,
      s: h.names.map((n) => ({ label: short(n), slot: Math.max(0, Number(n.replace("joint_", "")) - 1) })),
    };
  }, [history.data]);
  const s = detail.data;
  const exportList = (exports.data ?? []).filter((e) => e.session === id);

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardBody className="flex flex-row flex-wrap items-center gap-3">
          <Select
            aria-label={t("tel.session")}
            label={t("tel.session")}
            size="sm"
            className="max-w-md"
            selectedKeys={id ? [id] : []}
            onSelectionChange={(k) => setPicked((Array.from(k)[0] as string) ?? null)}
            data-testid="session-select"
          >
            {(sessions.data ?? []).map((x) => (
              <SelectItem key={x.id}>{`${new Date(x.started).toLocaleString()} · ${x.mode}${x.ended ? "" : " (open)"} · ${x.samples} samples`}</SelectItem>
            ))}
          </Select>
          {status.rec.available && (
            <Switch size="sm" isSelected={status.rec.recording} onValueChange={(v) => rec.mutate(v)} data-testid="rec-switch">
              {t("tel.recCurrent")}
            </Switch>
          )}
          <Button size="sm" variant="flat" isDisabled={!status.ros_running || bag.isPending} onPress={() => bag.mutate()} data-testid="record-bag">
            <i className="bi bi-record2" /> {t("tel.recordBag")}
          </Button>
        </CardBody>
      </Card>
      {job && <EnvJob id={job} />}
      {!id ? (
        <p className="text-sm opacity-80">{t("tel.noSessions")}</p>
      ) : (
        <>
          {s && (
            <Card data-testid="session-detail">
              <CardBody className="flex flex-col gap-2 text-sm">
                <div className="flex flex-wrap gap-x-6 gap-y-1">
                  <span>{s.mode} · {s.source}</span>
                  <span>{t("tel.started")}: {new Date(s.started).toLocaleString()}</span>
                  <span>{s.ended ? `${t("tel.ended")}: ${new Date(s.ended).toLocaleString()}` : t("tel.open")}</span>
                  <span>{t("tel.by")}: {s.user || "—"}</span>
                  <span className="font-mono">{s.git_commit.slice(0, 10)}</span>
                  <span data-testid="session-samples">{t("tel.samples", { n: s.samples, hz: s.rate_hz, dropped: s.dropped })}</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {(["parquet", "csv"] as const).map((f) => (
                    <Button key={f} size="sm" color="primary" variant="flat" isDisabled={exp.isPending} onPress={() => exp.mutate({ session: s.id, format: f })} data-testid={`export-${f}`}>
                      <i className="bi bi-box-arrow-down" /> {t("tel.export", { format: f.toUpperCase() })}
                    </Button>
                  ))}
                </div>
                {exportList.map((e) => (
                  <div key={e.name} className="flex flex-wrap items-center gap-2 text-xs" data-testid="export-row">
                    <span className="font-mono">{e.name}</span>
                    {e.files.map((f) => (
                      <a key={f.name} className="text-primary underline" href={exportFileUrl(e.name, f.name)}>
                        {f.name} ({(f.size / 1e3).toFixed(0)} kB)
                      </a>
                    ))}
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
          <Card>
            <CardHeader className="text-sm font-semibold">
              {t("tel.historyPositions", { bucket: history.data ? history.data.bucket_s.toFixed(2) : "…" })}
            </CardHeader>
            <CardBody className="pt-0">
              {data.s.length ? (
                <Chart data={data.d} series={data.s} unit="rad" testId="chart-history" />
              ) : (
                <p className="text-sm opacity-70">{t("tel.noSamples")}</p>
              )}
            </CardBody>
          </Card>
        </>
      )}
      <Card>
        <CardHeader className="text-sm font-semibold">{t("tel.recordings")}</CardHeader>
        <CardBody className="pt-0">
          {(recs.data ?? []).length === 0 ? (
            <p className="text-sm opacity-70">{t("tel.noRecordings")}</p>
          ) : (
            <table className="w-full text-sm" data-testid="recordings">
              <tbody>
                {recs.data!.map((r) => (
                  <tr key={r.id} className="border-t border-default-200">
                    <td className="py-1 pr-3 font-mono">{r.path}</td>
                    <td className="py-1 pr-3">{new Date(r.start).toLocaleString()}</td>
                    <td className="py-1 pr-3">{r.topics.length} topics</td>
                    <td className="py-1 text-right">{(r.size / 1e6).toFixed(1)} MB</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

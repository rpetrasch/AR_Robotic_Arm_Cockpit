"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, CardBody, Chip, Input, Spinner, Tab, Tabs } from "@heroui/react";
import {
  checkReachability,
  createScenario,
  getScenario,
  getSiteContext,
  saveScenario,
  validateScenario,
  type Problem,
} from "@/lib/api/scenarios";
import { ApiError } from "@/lib/api/http";
import { useStatus } from "@/lib/api/status";
import { useI18n } from "@/lib/i18n";
import { useAssistant, usePageContext, useScenarioTarget } from "@/lib/assistant";
import { Robot3D, type DetectedObject } from "@/components/monitoring/Robot3D";
import { FormEditor } from "@/components/scenarios/FormEditor";
import { SceneEditor } from "@/components/scenarios/SceneEditor";
import { YamlEditor } from "@/components/scenarios/YamlEditor";
import { NEW_SCENARIO, read, setIn, type Path, type SceneObject } from "@/components/scenarios/doc";

const OBJ_COLORS: Record<string, string> = { red: "#d64545", green: "#2f9e44", blue: "#3366cc", yellow: "#e0b000", orange: "#e8590c", grey: "#868e96", white: "#e9ecef", black: "#343a40" };

function restHeight(o: SceneObject): number {
  if (o.pose.z !== undefined) return o.pose.z;
  if (o.shape === "box") return (o.size[2] ?? 0.03) / 2;
  if (o.shape === "cylinder") return (o.size[1] ?? 0.05) / 2;
  return (o.size[0] ?? 0.04) / 2;
}

/** The scenario editor (docs/specs/spec_cockpit.md §12.3): Form · Scene · YAML · 3D, validation as you
 * type, Save (blocked while invalid), Check reachability. `?id=` edits, without it: new. */
export default function EditScenarioPage() {
  const { t } = useI18n();
  const assistant = useAssistant();
  const [helpText, setHelpText] = useState("");
  const [applied, setApplied] = useState(false);
  const { status } = useStatus();
  const [id, setId] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [problems, setProblems] = useState<{ errors: Problem[]; warnings: Problem[] }>({ errors: [], warnings: [] });
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [reach, setReach] = useState<Awaited<ReturnType<typeof checkReachability>> | null>(null);
  const site = useQuery({ queryKey: ["scenario-context"], queryFn: getSiteContext });

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("id");
    setId(q);
    if (!q) setText(NEW_SCENARIO);
    else getScenario(q).then((r) => setText(r.yaml)).catch((e) => setMsg({ text: String(e.message), error: true }));
  }, []);

  useEffect(() => {
    if (text === null) return;
    const h = setTimeout(() => {
      validateScenario(text)
        .then((r) => setProblems({ errors: r.errors, warnings: r.warnings }))
        .catch(() => undefined);
    }, 400);
    return () => clearTimeout(h);
  }, [text]);

  const parsed = useMemo(() => (text === null ? { data: null, error: null } : read(text)), [text]);
  const data = parsed.data;
  const set = (path: Path, value: unknown) => setText((old) => (old === null ? old : setIn(old, path, value)));

  const save = useMutation({
    mutationFn: () => (id ? saveScenario(id, text!) : createScenario(text!)),
    onSuccess: (r) => {
      setMsg({ text: t("sim.saved", { id: r.id, v: r.version }) });
      if (!id) {
        setId(r.id);
        window.history.replaceState(null, "", `/simulations/edit/?id=${r.id}`);
      }
      getScenario(r.id).then((s) => setText(s.yaml));
    },
    onError: (e: Error) => {
      const extra = e instanceof ApiError && Array.isArray(e.problem.errors) ? ` ${(e.problem.errors as Problem[]).map((p) => `line ${p.line}: ${p.message}`).join("; ")}` : "";
      setMsg({ text: e.message + extra, error: true });
    },
  });
  const reachable = useMutation({
    mutationFn: () => checkReachability(id ?? data?.id ?? "new", text!),
    onSuccess: setReach,
    onError: (e: Error) => setMsg({ text: e.message, error: true }),
  });

  // "Help me write this scenario" (§14.1): the answer's YAML replaces the editor text, unsaved
  useScenarioTarget((yaml) => {
    setText(yaml);
    setApplied(true);
  });
  usePageContext({ scenario_id: id ?? "", yaml: text ?? "" });
  if (text === null || !site.data) return <Spinner label={t("common.loading")} />;
  const named = site.data.named_poses.arm ?? {};
  const startJoints = named[data?.start?.named_pose ?? "home"] ?? {};
  const preview: DetectedObject[] = [...(data?.scene?.objects ?? []), ...(data?.scene?.obstacles ?? [])].map((o) => ({
    id: o.id,
    class: o.class ?? o.shape,
    frame: "table",
    xyz: [o.pose.x, o.pose.y, restHeight(o)],
    color: typeof o.color === "string" ? OBJ_COLORS[o.color] : undefined,
    shape: o.shape,
    size: o.size,
    yaw: ((o.pose.yaw_deg ?? 0) * Math.PI) / 180,
  }));
  const onSceneChange = (list: "objects" | "obstacles", i: number, value: SceneObject | undefined) => {
    const items = [...((data?.scene?.[list] as SceneObject[] | undefined) ?? [])];
    if (value === undefined) items.splice(i, 1);
    else items[i] = value;
    set(["scene", list], items);
  };

  const askHelp = () => {
    if (text === null) return;
    assistant.ask({ question: helpText.trim() || t("sim.helpDefault"), task: "scenario_help", context: { scenario_id: id ?? "", yaml: text } });
    setHelpText("");
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/simulations/" className="text-sm text-primary underline">
          ← {t("nav.simulations")}
        </Link>
        <h1 className="text-2xl font-semibold" data-testid="editor-title">
          {data?.name ?? id ?? t("sim.new")}
        </h1>
        {data?.version !== undefined && <Chip size="sm" variant="flat">v{data.version}</Chip>}
        <Chip size="sm" color={problems.errors.length ? "danger" : "success"} variant="flat" data-testid="validity">
          {problems.errors.length ? t("sim.errors", { n: problems.errors.length }) : t("sim.valid")}
        </Chip>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="flat" isDisabled={!status?.ros_running} isLoading={reachable.isPending} onPress={() => reachable.mutate()} data-testid="check-reachability">
            <i className="bi bi-bullseye" /> {t("sim.checkReach")}
          </Button>
          <Button size="sm" color="primary" isDisabled={problems.errors.length > 0 || !!parsed.error} isLoading={save.isPending} onPress={() => save.mutate()} data-testid="save">
            <i className="bi bi-save" /> {t("common.save")}
          </Button>
          {id && (
            <Button as={Link} href={`/simulations/run/?id=${id}`} size="sm" variant="flat" color="primary" data-testid="to-run">
              <i className="bi bi-play-fill" /> {t("sim.run")}
            </Button>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2" data-testid="scenario-help">
        <Input
          size="sm"
          className="max-w-xl"
          value={helpText}
          onValueChange={setHelpText}
          placeholder={t("sim.helpPlaceholder")}
          aria-label={t("sim.helpPlaceholder")}
          onKeyDown={(e) => { if (e.key === "Enter") askHelp(); }}
          startContent={<i className="bi bi-stars text-secondary" />}
          data-testid="scenario-help-input"
        />
        <Button size="sm" variant="flat" color="secondary" onPress={askHelp} isDisabled={text === null} data-testid="scenario-help-ask">
          {t("sim.helpWrite")}
        </Button>
        {applied && <span className="text-xs text-warning-600" data-testid="scenario-help-applied">{t("sim.helpApplied")}</span>}
      </div>
      {msg && (
        <p className={`text-sm ${msg.error ? "text-danger" : "text-success"}`} role="status" data-testid="editor-msg">
          {msg.text}
        </p>
      )}
      {reach && (
        <Card data-testid="reachability">
          <CardBody className="gap-1 text-sm">
            <span className="font-medium">{reach.reachable ? t("sim.allReachable") : t("sim.notReachable")}</span>
            {reach.targets.map((tg) => (
              <span key={tg.step} className={tg.reachable ? "" : "text-danger"} data-testid={`reach-${tg.step}`}>
                {t("sim.reachStep", { n: tg.step + 1, x: tg.x, y: tg.y, z: tg.z })}: {tg.reachable ? "ok" : tg.error}
              </span>
            ))}
          </CardBody>
        </Card>
      )}
      {(problems.errors.length > 0 || problems.warnings.length > 0) && (
        <Card data-testid="problems">
          <CardBody className="gap-1 text-sm">
            {[...problems.errors, ...problems.warnings].map((p, i) => (
              <span key={i} className={p.severity === "error" ? "text-danger" : "text-warning"}>
                <i className={`bi ${p.severity === "error" ? "bi-x-circle" : "bi-exclamation-triangle"}`} /> {p.line ? `line ${p.line}: ` : ""}
                <span className="font-mono">{p.path}</span> {p.message}
              </span>
            ))}
          </CardBody>
        </Card>
      )}
      <Tabs aria-label={t("sim.editor")} data-testid="editor-tabs">
        <Tab key="form" title={t("sim.tabForm")}>
          {data ? <FormEditor data={data} site={site.data} set={set} /> : <p className="text-danger">{parsed.error}</p>}
        </Tab>
        <Tab key="scene" title={t("sim.tabScene")} data-testid="tab-scene">
          {data ? <SceneEditor data={data} site={site.data} onChange={onSceneChange} /> : <p className="text-danger">{parsed.error}</p>}
        </Tab>
        <Tab key="yaml" title="YAML" data-testid="tab-yaml">
          <YamlEditor value={text} onChange={setText} dark={typeof document !== "undefined" && document.documentElement.classList.contains("dark")} />
        </Tab>
        <Tab key="3d" title={t("sim.tab3d")} data-testid="tab-3d">
          <Robot3D joints={startJoints} objects={preview} />
          <p className="mt-2 text-xs opacity-70">{t("sim.previewHint", { pose: data?.start?.named_pose ?? "home" })}</p>
        </Tab>
      </Tabs>
    </div>
  );
}

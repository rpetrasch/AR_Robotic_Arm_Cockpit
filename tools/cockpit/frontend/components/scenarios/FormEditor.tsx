"use client";

import { Button, Card, CardBody, CardHeader, Checkbox, CheckboxGroup, Input, Select, SelectItem, Switch, Textarea } from "@heroui/react";
import type { SiteContext } from "@/lib/api/scenarios";
import { useI18n } from "@/lib/i18n";
import { stepKind, type Path, type Scenario, type Step } from "./doc";

const STEP_KINDS = ["move_named", "move_pose", "move_joints", "gripper", "wait"] as const;
const DEFAULT_STEP: Record<(typeof STEP_KINDS)[number], Step> = {
  move_named: { move_named: "observe" },
  move_pose: { move_pose: { frame: "table", x: 0.1, y: 0.0, z: 0.2, rpy_deg: [180, 0, -90] } },
  move_joints: { move_joints: [0, 0, 0, 0, 0, 0] },
  gripper: { gripper: "open" },
  wait: { wait: 1 },
};
const num = (v: string) => (v === "" ? undefined : Number(v));

/** The form pane of the editor (docs/specs/spec_cockpit.md §12.3): metadata, start, steps, planning,
 * success. Every change goes into the YAML text through `set` (comments are kept). */
export function FormEditor({ data, site, set }: { data: Scenario; site: SiteContext; set: (path: Path, value: unknown) => void }) {
  const { t } = useI18n();
  const steps = data.task?.steps ?? [];
  const named = Object.keys(site.named_poses.arm ?? {});
  const gripperStates = Object.keys(site.named_poses.gripper ?? {});
  const taskTypes = site.capabilities?.task_types ?? ["sequence"];
  const setSteps = (next: Step[]) => set(["task", "steps"], next);
  const move = (i: number, d: number) => {
    const next = [...steps];
    const [s] = next.splice(i, 1);
    next.splice(i + d, 0, s);
    setSteps(next);
  };

  return (
    <div className="flex flex-col gap-4" data-testid="form-editor">
      <Card>
        <CardHeader className="font-semibold">{t("sim.metadata")}</CardHeader>
        <CardBody className="grid gap-3 pt-0 md:grid-cols-2">
          <Input size="sm" label="id" value={data.id ?? ""} onValueChange={(v) => set(["id"], v)} description={t("sim.idHint")} data-testid="f-id" />
          <Input size="sm" label={t("sim.name")} value={data.name ?? ""} onValueChange={(v) => set(["name"], v)} data-testid="f-name" />
          <Textarea size="sm" className="md:col-span-2" label={t("sim.description")} value={data.description ?? ""} onValueChange={(v) => set(["description"], v)} minRows={2} />
          <Input size="sm" label={t("sim.tags")} value={(data.tags ?? []).join(", ")}
            onValueChange={(v) => set(["tags"], v.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean))} />
          <CheckboxGroup size="sm" orientation="horizontal" label={t("sim.modes")} value={data.modes ?? []} onValueChange={(v) => set(["modes"], v)}>
            <Checkbox value="sim_gazebo">sim_gazebo</Checkbox>
            <Checkbox value="sim_mock">sim_mock</Checkbox>
            <Checkbox value="real">real</Checkbox>
          </CheckboxGroup>
          <Select size="sm" label={t("sim.start")} selectedKeys={[data.start?.named_pose ?? "home"]}
            onSelectionChange={(k) => set(["start"], { named_pose: Array.from(k)[0] as string })}>
            {named.map((n) => <SelectItem key={n}>{n}</SelectItem>)}
          </Select>
          <Select size="sm" label={t("sim.taskType")} selectedKeys={[data.task?.type ?? "sequence"]}
            onSelectionChange={(k) => set(["task", "type"], Array.from(k)[0] as string)}
            disabledKeys={["pick_place", "sort"].filter((x) => !taskTypes.includes(x))}
            description={t("sim.taskTypeHint")}>
            {["sequence", "pick_place", "sort"].map((x) => <SelectItem key={x}>{x}</SelectItem>)}
          </Select>
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex items-center gap-2 font-semibold">
          {t("sim.steps", { n: steps.length })}
          <Select aria-label={t("sim.addStep")} size="sm" className="ml-auto max-w-48" placeholder={t("sim.addStep")} selectedKeys={[]}
            onSelectionChange={(k) => {
              const kind = Array.from(k)[0] as (typeof STEP_KINDS)[number];
              if (kind) setSteps([...steps, DEFAULT_STEP[kind]]);
            }} data-testid="add-step">
            {STEP_KINDS.map((x) => <SelectItem key={x}>{x}</SelectItem>)}
          </Select>
        </CardHeader>
        <CardBody className="flex flex-col gap-2 pt-0">
          {steps.map((s, i) => {
            const kind = stepKind(s);
            const path = ["task", "steps", i, kind];
            return (
              <div key={i} className="flex flex-wrap items-end gap-2 rounded-lg border border-default-200 p-2" data-testid={`step-${i}`}>
                <span className="w-6 self-center text-right font-mono">{i + 1}</span>
                <span className="w-24 self-center font-mono text-xs">{kind}</span>
                {kind === "move_named" && (
                  <Select size="sm" aria-label="pose" className="w-40" selectedKeys={[(s as { move_named: string }).move_named]}
                    onSelectionChange={(k) => set(path, Array.from(k)[0])}>
                    {named.map((n) => <SelectItem key={n}>{n}</SelectItem>)}
                  </Select>
                )}
                {kind === "gripper" && (
                  <Select size="sm" aria-label="gripper" className="w-32" selectedKeys={[String((s as { gripper: string }).gripper)]}
                    onSelectionChange={(k) => set(path, Array.from(k)[0])}>
                    {gripperStates.map((n) => <SelectItem key={n}>{n}</SelectItem>)}
                  </Select>
                )}
                {kind === "wait" && (
                  <Input size="sm" type="number" className="w-28" label="s" value={String((s as { wait: number }).wait)} onValueChange={(v) => set(path, num(v) ?? 0)} />
                )}
                {kind === "move_joints" &&
                  (s as { move_joints: number[] }).move_joints.map((v, j) => (
                    <Input key={j} size="sm" type="number" step="0.01" className="w-20" label={`j${j + 1}`} value={String(v)} onValueChange={(x) => set([...path, j], num(x) ?? 0)} />
                  ))}
                {kind === "move_pose" && (() => {
                  const p = (s as { move_pose: { x: number; y: number; z: number; rpy_deg?: number[]; planner?: string } }).move_pose;
                  return (
                    <>
                      {(["x", "y", "z"] as const).map((f) => (
                        <Input key={f} size="sm" type="number" step="0.01" className="w-20" label={f} value={String(p[f])} onValueChange={(v) => set([...path, f], num(v) ?? 0)} />
                      ))}
                      <Input size="sm" className="w-32" label="rpy°" value={(p.rpy_deg ?? [180, 0, -90]).join(", ")}
                        onValueChange={(v) => {
                          const r = v.split(",").map((x) => Number(x.trim()));
                          if (r.length === 3 && r.every((x) => !Number.isNaN(x))) set([...path, "rpy_deg"], r);
                        }} />
                      <Select size="sm" aria-label="planner" className="w-32" selectedKeys={[p.planner ?? "ompl"]}
                        onSelectionChange={(k) => set([...path, "planner"], Array.from(k)[0])}>
                        {["ompl", "pilz_lin", "pilz_ptp"].map((x) => <SelectItem key={x}>{x}</SelectItem>)}
                      </Select>
                    </>
                  );
                })()}
                <div className="ml-auto flex gap-1">
                  <Button size="sm" isIconOnly variant="light" isDisabled={i === 0} onPress={() => move(i, -1)} aria-label="up"><i className="bi bi-arrow-up" /></Button>
                  <Button size="sm" isIconOnly variant="light" isDisabled={i === steps.length - 1} onPress={() => move(i, 1)} aria-label="down"><i className="bi bi-arrow-down" /></Button>
                  <Button size="sm" isIconOnly variant="light" color="danger" onPress={() => setSteps(steps.filter((_x, j) => j !== i))} aria-label="remove"><i className="bi bi-x-lg" /></Button>
                </div>
              </div>
            );
          })}
        </CardBody>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="font-semibold">{t("sim.planning")}</CardHeader>
          <CardBody className="grid grid-cols-2 gap-2 pt-0">
            <Select size="sm" label="pipeline" selectedKeys={[data.planning?.pipeline ?? "ompl"]} onSelectionChange={(k) => set(["planning", "pipeline"], Array.from(k)[0])}>
              {["ompl", "pilz"].map((x) => <SelectItem key={x}>{x}</SelectItem>)}
            </Select>
            <Input size="sm" label="planner_id" value={data.planning?.planner_id ?? ""} onValueChange={(v) => set(["planning", "planner_id"], v || undefined)} />
            <Input size="sm" type="number" step="0.05" label="velocity_scaling" value={String(data.planning?.velocity_scaling ?? site.motion.velocity_scaling ?? 0.2)}
              onValueChange={(v) => set(["planning", "velocity_scaling"], num(v))} />
            <Input size="sm" type="number" step="0.05" label="acceleration_scaling" value={String(data.planning?.acceleration_scaling ?? site.motion.acceleration_scaling ?? 0.2)}
              onValueChange={(v) => set(["planning", "acceleration_scaling"], num(v))} />
            <Input size="sm" type="number" label="planning_time_s" value={String(data.planning?.planning_time_s ?? 5)} onValueChange={(v) => set(["planning", "planning_time_s"], num(v))} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader className="font-semibold">{t("sim.success")}</CardHeader>
          <CardBody className="flex flex-col gap-2 pt-0">
            <Switch size="sm" isSelected={data.success?.all_steps_succeed ?? true} onValueChange={(v) => set(["success", "all_steps_succeed"], v)}>
              all_steps_succeed
            </Switch>
            <Input size="sm" type="number" label="max_duration_s" value={String(data.success?.max_duration_s ?? "")} onValueChange={(v) => set(["success", "max_duration_s"], num(v))} />
            <Input size="sm" label="objects_in_bins" description={t("sim.inBinsHint")}
              value={Object.entries(data.success?.objects_in_bins ?? {}).map(([o, b]) => `${o}=${b}`).join(", ")}
              onValueChange={(v) => {
                const pairs = v.split(",").map((x) => x.split("=").map((y) => y.trim())).filter((p) => p.length === 2 && p[0] && p[1]);
                set(["success", "objects_in_bins"], pairs.length ? Object.fromEntries(pairs) : undefined);
              }} />
            <Input size="sm" type="number" step="0.005" label="randomize xy_jitter_m" value={String(data.scene?.randomize?.xy_jitter_m ?? "")}
              onValueChange={(v) => set(["scene", "randomize"], num(v) ? { ...(data.scene?.randomize ?? {}), xy_jitter_m: num(v) } : undefined)} />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

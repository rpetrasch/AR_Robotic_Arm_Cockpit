"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Checkbox, Chip, Spinner } from "@heroui/react";
import { disarm, getArmState, getJoints, preflight, type ArmState, type PreflightStep } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { StatusIcon } from "@/components/setup/StatusIcon";
import { RobotPose } from "@/components/environment/RobotPose";

const ICON = { pending: "unknown", running: "running", pass: "pass", fail: "fail", overridden: "warn" } as const;
const ESTOP_S = 60;

/**
 * The REAL pre-flight wizard (docs/specs/spec_cockpit.md §9.4): devices, e-stop test, workspace,
 * homing, pose check, limits, then Arm. The backend enforces the order and logs every action;
 * this page polls the state while the e-stop test waits.
 */
export default function PreflightPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const state = useQuery({
    queryKey: ["arm"],
    queryFn: getArmState,
    refetchInterval: (q) => ((q.state.data as ArmState | undefined)?.estop_phase ? 1000 : 3000),
  });
  const [error, setError] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: ({ step, action }: { step: string; action?: string }) => preflight(step, action),
    onSuccess: (data) => {
      setError(null);
      qc.setQueryData(["arm"], data);
    },
    onError: (err: Error) => setError(err.message),
  });
  const off = useMutation({ mutationFn: disarm, onSuccess: (data) => qc.setQueryData(["arm"], data) });

  if (!state.data) return <Spinner label={t("common.loading")} />;
  const s = state.data;
  if (!s.real) {
    return (
      <Card>
        <CardHeader className="font-semibold">{t("pf.title")}</CardHeader>
        <CardBody className="pt-0">
          <p data-testid="pf-no-real">{t("pf.noReal")}</p>
          <Link href="/environment/" className="text-primary underline">
            {t("nav.environment")}
          </Link>
        </CardBody>
      </Card>
    );
  }
  const run = (step: string, action?: string) => act.mutate({ step, action });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{t("pf.title")}</h1>
        <Chip color={s.arm === "ARMED" ? "success" : s.arm === "E_STOP" ? "danger" : "warning"} data-testid="pf-arm">
          {(s.arm ?? "").replace("_", " ")}
        </Chip>
        <span className="text-sm opacity-80">{s.container}</span>
      </div>
      <p className="text-sm">{t("pf.intro")}</p>
      {s.override && (
        <p className="rounded bg-danger px-3 py-1 text-sm text-white" role="alert">
          {t("pf.overrideBanner")}
        </p>
      )}
      {error && (
        <p className="text-sm text-danger" role="alert" data-testid="pf-error">
          {error}
        </p>
      )}
      {s.steps.map((step, i) => (
        <Card key={step.id} data-testid={`pf-${step.id}`} className={s.next === step.id ? "border-2 border-primary" : ""}>
          <CardHeader className="flex items-center gap-2">
            <StatusIcon status={ICON[step.status]} />
            <span className="font-semibold">
              {i + 1}. {step.title}
            </span>
            <span className="text-sm opacity-70">{step.description}</span>
          </CardHeader>
          <CardBody className="flex flex-col gap-2 pt-0">
            {step.detail && (
              <p className="text-sm" data-testid={`pf-${step.id}-detail`}>
                {step.detail}
                {step.user ? ` (${step.user})` : ""}
              </p>
            )}
            {s.next === step.id && <StepActions step={step} state={s} run={run} busy={act.isPending} />}
          </CardBody>
        </Card>
      ))}
      <div className="flex gap-2">
        <Button color="success" isDisabled={!s.can_arm || act.isPending} onPress={() => run("arm")} data-testid="pf-arm-button">
          {t("pf.arm")}
        </Button>
        {s.arm === "ARMED" && (
          <Button variant="flat" onPress={() => off.mutate()} data-testid="pf-disarm">
            {t("pf.disarm")}
          </Button>
        )}
      </div>
      {s.arm === "ARMED" && <p className="text-sm text-success">{t("pf.armed")}</p>}
    </div>
  );
}

function StepActions({
  step,
  state,
  run,
  busy,
}: {
  step: PreflightStep;
  state: ArmState;
  run: (step: string, action?: string) => void;
  busy: boolean;
}) {
  const { t } = useI18n();
  const [ok, setOk] = useState(false);
  switch (step.id) {
    case "devices":
      return (
        <Button size="sm" color="primary" isLoading={busy} onPress={() => run("devices")} data-testid="pf-devices-run">
          {t("pf.devicesRun")}
        </Button>
      );
    case "estop":
      return <EstopActions state={state} run={run} busy={busy} />;
    case "workspace":
      return (
        <div className="flex items-center gap-3">
          <Checkbox isSelected={ok} onValueChange={setOk} data-testid="pf-workspace-check">
            {t("pf.workspaceConfirm")}
          </Checkbox>
          <Button size="sm" color="primary" isDisabled={!ok} onPress={() => run("workspace", "confirm")} data-testid="pf-workspace-confirm">
            {t("pf.confirm")}
          </Button>
        </div>
      );
    case "homing":
      return (
        <Button size="sm" color="primary" isLoading={busy} onPress={() => run("homing")} data-testid="pf-homing-run">
          {t("pf.home")}
        </Button>
      );
    case "pose":
      return <PoseCheck onConfirm={() => run("pose", "confirm")} />;
    case "limits":
      return <Limits state={state} onConfirm={() => run("limits", "confirm")} />;
  }
}

function EstopActions({ state, run, busy }: { state: ArmState; run: (step: string, action?: string) => void; busy: boolean }) {
  const { t } = useI18n();
  const [left, setLeft] = useState(ESTOP_S);
  useEffect(() => {
    if (state.estop_phase !== "press") return;
    setLeft(ESTOP_S);
    const timer = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(timer);
  }, [state.estop_phase]);
  if (state.estop_phase === "press") {
    return (
      <p className="flex items-center gap-2 font-semibold text-danger" data-testid="pf-estop-press">
        <Spinner size="sm" color="danger" /> {t("pf.estopPress", { s: left })}
      </p>
    );
  }
  if (state.estop_phase === "reset") {
    return (
      <div className="flex items-center gap-3">
        <span className="font-semibold">{t("pf.estopReset")}</span>
        <Button size="sm" color="primary" isLoading={busy} onPress={() => run("estop", "reset")} data-testid="pf-estop-reset">
          {t("pf.reset")}
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-2">
      <Button size="sm" color="primary" onPress={() => run("estop", "start")} data-testid="pf-estop-start">
        {t("pf.estopStart")}
      </Button>
      {state.override && (
        <Button size="sm" color="danger" variant="flat" onPress={() => run("estop", "override")} data-testid="pf-estop-override">
          {t("pf.override")}
        </Button>
      )}
    </div>
  );
}

function PoseCheck({ onConfirm }: { onConfirm: () => void }) {
  const { t } = useI18n();
  const joints = useQuery({ queryKey: ["joints"], queryFn: getJoints, refetchInterval: 1000, retry: false });
  const values = Object.fromEntries((joints.data?.name ?? []).map((n, i) => [n, joints.data!.position[i]]));
  return (
    <div className="flex flex-col gap-2">
      {joints.error ? <p className="text-sm text-danger">{t("pf.noJoints", { detail: joints.error.message })}</p> : <RobotPose joints={values} />}
      <p className="text-xs opacity-70">{t("pf.cameras")}</p>
      <div>
        <Button size="sm" color="primary" isDisabled={!joints.data} onPress={onConfirm} data-testid="pf-pose-confirm">
          {t("pf.poseConfirm")}
        </Button>
      </div>
    </div>
  );
}

function Limits({ state, onConfirm }: { state: ArmState; onConfirm: () => void }) {
  const { t } = useI18n();
  const l = state.limits;
  if (!l) return null;
  return (
    <div className="flex flex-col gap-2 text-sm">
      <div className="grid max-w-md grid-cols-2 gap-x-4 gap-y-1">
        <span>{t("pf.velocity")}</span>
        <b data-testid="pf-velocity">{l.velocity_scaling}</b>
        <span>{t("pf.acceleration")}</span>
        <b>{l.acceleration_scaling}</b>
        <span>{t("pf.table")}</span>
        <b>
          {l.table_size_m[0]} × {l.table_size_m[1]} m
        </b>
      </div>
      <p className="font-medium">{t("pf.jointLimits")}</p>
      <div className="grid max-w-md grid-cols-3 gap-x-4">
        {Object.entries(l.joint_limits_deg).map(([j, [lo, hi]]) => (
          <span key={j} className="contents">
            <span>{j}</span>
            <span>{lo}</span>
            <span>{hi}</span>
          </span>
        ))}
      </div>
      <p className="text-xs opacity-70">{t("pf.limitsSource", { source: l.source })}</p>
      <div>
        <Button size="sm" color="primary" onPress={onConfirm} data-testid="pf-limits-confirm">
          {t("pf.limitsConfirm")}
        </Button>
      </div>
    </div>
  );
}

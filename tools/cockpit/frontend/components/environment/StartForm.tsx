"use client";

import { useState } from "react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Radio,
  RadioGroup,
  Switch,
} from "@heroui/react";
import type { ModeRow, Status } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { useAssistant } from "@/lib/assistant";
import { CliLine } from "@/components/common/CopyButton";

type Robot = "mock" | "gazebo" | "remote";
type View = "windows" | "browser" | "none";

/** The mode id for the form's choices; the ids are the backend's (environment/modes.py). */
export function pickMode(os: string, robot: Robot, rviz: boolean, view: View, gpu: boolean): string {
  if (robot === "remote") return "remote";
  const r = robot === "gazebo" ? "gazebo" : "mock";
  if (os !== "linux") return rviz ? `sim_${r}_rviz` : `sim_${r}`;
  if (view === "browser") return `sim_${r}_rviz`;
  return `linux_${r}${view === "none" ? "_headless" : ""}${gpu ? "_gpu" : ""}`;
}

export interface StartRequest {
  mode: string;
  lichtblick: boolean;
  switch: boolean;
  confirm: boolean;
}

/**
 * "Start a stack" (docs/specs/spec_cockpit.md §3.2, §9.1): Robot and viewer controls, the resulting
 * command live, Start. A running stack turns Start into "Stop and switch" (§3.1).
 */
export function StartForm({
  status,
  modes,
  busy,
  onStart,
}: {
  status: Status;
  modes: ModeRow[];
  busy: boolean;
  onStart: (req: StartRequest) => void;
}) {
  const { t } = useI18n();
  const os = status.host.os;
  const assistant = useAssistant();
  const [robot, setRobot] = useState<Robot>("mock");
  const [rviz, setRviz] = useState(false);
  const [view, setView] = useState<View>("windows");
  const [gpu, setGpu] = useState(false);
  const [lichtblick, setLichtblick] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const id = pickMode(os, robot, rviz, view, gpu);
  const mode = modes.find((m) => m.id === id);
  const command = mode?.start_command ? `${mode.start_command}${lichtblick ? " LICHTBLICK=1" : ""}` : "";
  const running = status.mode;
  const switching = !!running;
  const request = (): StartRequest => ({ mode: id, lichtblick, switch: switching, confirm: running?.robot === "real" });

  return (
    <Card data-testid="start-form">
      <CardHeader className="flex flex-col items-start gap-1">
        <span className="font-semibold">{t("env.startTitle")}</span>
        <span className="text-sm opacity-80">{t("env.startIntro")}</span>
      </CardHeader>
      <CardBody className="flex flex-col gap-4 pt-0">
        <div className="flex flex-wrap gap-8">
          <RadioGroup label={t("env.robot")} value={robot} onValueChange={(v) => setRobot(v as Robot)} data-testid="robot">
            <Radio value="mock" description={t("env.robotMockHint")} data-testid="robot-mock">
              {t("env.robotMock")}
            </Radio>
            <Radio value="gazebo" description={t("env.robotGazeboHint")} data-testid="robot-gazebo">
              {t("env.robotGazebo")}
            </Radio>
            {os === "macos" && (
              <Radio value="remote" description={t("env.robotRemoteHint")} data-testid="robot-remote">
                {t("env.robotRemote")}
              </Radio>
            )}
          </RadioGroup>
          <div className="flex flex-col gap-3">
            {os === "linux" ? (
              <>
                <RadioGroup label={t("env.view")} value={view} onValueChange={(v) => setView(v as View)} isDisabled={robot === "remote"}>
                  <Radio value="windows" data-testid="view-windows">{t("env.viewWindows")}</Radio>
                  <Radio value="browser" data-testid="view-browser">{t("env.viewBrowser")}</Radio>
                  <Radio value="none" data-testid="view-none">{t("env.viewNone")}</Radio>
                </RadioGroup>
                <Switch size="sm" isSelected={gpu} onValueChange={setGpu} isDisabled={view === "browser"} data-testid="gpu">
                  {t("env.gpu")}
                </Switch>
              </>
            ) : (
              <Switch size="sm" isSelected={rviz} onValueChange={setRviz} isDisabled={robot === "remote"} data-testid="rviz">
                {t("env.rvizBrowser")}
              </Switch>
            )}
            <Switch size="sm" isSelected={lichtblick} onValueChange={setLichtblick} data-testid="lichtblick">
              {t("env.lichtblickWeb")}
            </Switch>
          </div>
        </div>
        {robot === "remote" && <p className="text-sm opacity-80">{t("env.remoteNote")}</p>}
        <div className="flex flex-col gap-1">
          <span className="text-sm">{t("env.willRun")}</span>
          <div data-testid="start-command">
            <CliLine command={switching ? `make down && ${command}` : command} />
          </div>
          <span className="text-xs opacity-70">{t("env.detachNote")}</span>
          {os === "linux" && view === "windows" && robot !== "remote" && (
            <span className="text-xs opacity-70">{t("env.linuxWindows")}</span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            color={switching ? "warning" : "primary"}
            isDisabled={!mode?.startable || busy}
            onPress={() => (switching ? setConfirm(true) : onStart(request()))}
            startContent={<i className={`bi ${switching ? "bi-arrow-left-right" : "bi-play-fill"}`} />}
            data-testid="start"
          >
            {switching ? t("env.switch") : t("env.start")}
          </Button>
          <Button
            variant="light"
            color="secondary"
            startContent={<i className="bi bi-stars" />}
            onPress={() => assistant.ask({ question: t("env.explainQuestion", { command }), task: "chat", context: { mode_id: id } })}
            data-testid="explain-mode"
          >
            {t("env.explainMode")}
          </Button>
        </div>
      </CardBody>
      <Modal isOpen={confirm} onOpenChange={setConfirm}>
        <ModalContent>
          <ModalHeader>{t("env.switchTitle")}</ModalHeader>
          <ModalBody>
            <p>{t("env.switchBody", { chip: running?.chip ?? "" })}</p>
            {running?.robot === "real" && <p className="text-danger">{t("env.switchReal")}</p>}
            <CliLine command={`make down && ${command}`} />
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={() => setConfirm(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              color="warning"
              onPress={() => {
                setConfirm(false);
                onStart(request());
              }}
              data-testid="confirm-switch"
            >
              {t("env.switch")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </Card>
  );
}

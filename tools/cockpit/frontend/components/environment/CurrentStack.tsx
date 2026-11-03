"use client";

import { useState } from "react";
import Link from "next/link";
import { Button, Card, CardBody, CardHeader, Chip, Modal, ModalBody, ModalContent, ModalFooter, ModalHeader } from "@heroui/react";
import type { Status } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";
import { HealthDot } from "./HealthDot";

/** The running stack (from Docker labels: also when started from the CLI), Stop, Restart. */
export function CurrentStack({
  status,
  busy,
  onStop,
  onRestart,
  compact,
}: {
  status: Status;
  busy: boolean;
  onStop: (confirm: boolean) => void;
  onRestart: () => void;
  /** Without the "Started with" label (the log tab needs the height). */
  compact?: boolean;
}) {
  const { t } = useI18n();
  const [confirm, setConfirm] = useState(false);
  const running = status.mode;
  const real = running?.robot === "real";
  const waiting = status.components.filter((c) => ["waiting", "error"].includes(c.status) && c.id !== "ollama");
  return (
    <Card data-testid="env-current">
      <CardHeader className="font-semibold">{t("env.current")}</CardHeader>
      <CardBody className="flex flex-col gap-2 pt-0">
        {running ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Chip color="primary" variant="flat" data-testid="env-current-chip">
                {running.chip}
              </Chip>
              {running.viewers && <Chip variant="flat">{running.viewers}</Chip>}
              <span className="text-sm opacity-80">
                {running.container} · {running.status}
              </span>
              {status.ready ? (
                <Chip size="sm" color="success" variant="dot" data-testid="env-ready">
                  {t("env.ready")}
                </Chip>
              ) : (
                waiting.length > 0 && (
                  <span className="flex items-center gap-1 text-sm" data-testid="env-waiting">
                    <HealthDot status="waiting" />
                    {t("env.waiting", { items: waiting.map((c) => c.id).join(", ") })}
                  </span>
                )
              )}
              <div className="ml-auto flex gap-2">
                {real && (
                  <Button as={Link} href="/environment/preflight/" size="sm" variant="flat" color="warning" data-testid="open-preflight">
                    {t("env.preflight")}
                  </Button>
                )}
                <Button size="sm" variant="flat" isDisabled={busy} onPress={onRestart} startContent={<i className="bi bi-arrow-repeat" />} data-testid="env-restart">
                  {t("env.restart")}
                </Button>
                <Button
                  size="sm"
                  color="danger"
                  variant="flat"
                  isDisabled={busy}
                  onPress={() => (real ? setConfirm(true) : onStop(false))}
                  startContent={<i className="bi bi-stop-fill" />}
                  data-testid="env-stop"
                >
                  {t("env.stop")}
                </Button>
              </div>
            </div>
            {running.command && (
              <>
                {!compact && <p className="text-sm">{t("env.startedWith")}</p>}
                <CliLine command={running.command} />
              </>
            )}
          </>
        ) : (
          <p className="text-sm">{t("env.none")}</p>
        )}
        {status.reasons.map((r) => (
          <p key={r} className="text-sm opacity-80">
            {r}
          </p>
        ))}
      </CardBody>
      <Modal isOpen={confirm} onOpenChange={setConfirm}>
        <ModalContent>
          <ModalHeader>{t("env.stopConfirmTitle")}</ModalHeader>
          <ModalBody>
            <p>{t("env.stopConfirm")}</p>
            <CliLine command="make down" />
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={() => setConfirm(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              color="danger"
              onPress={() => {
                setConfirm(false);
                onStop(true);
              }}
              data-testid="confirm-stop"
            >
              {t("env.stop")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </Card>
  );
}

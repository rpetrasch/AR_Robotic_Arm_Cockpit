"use client";

import { Card, CardBody } from "@heroui/react";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";
import { Brand } from "./TopBar";

/** Shown when the backend (and with it the DB in Docker) is gone (docs/specs/spec_cockpit.md §8.1). */
export function BackendDownPage() {
  const { t } = useI18n();
  return (
    <div className="flex min-h-screen items-center justify-center p-6" data-testid="backend-down">
      <Card className="max-w-lg">
        <CardBody className="flex flex-col gap-3 p-6">
          <Brand />
          <h1 className="text-xl font-semibold text-danger">{t("status.backendDown")}</h1>
          <p>{t("status.backendDownHint")}</p>
          <CliLine command="./cockpit.sh" />
        </CardBody>
      </Card>
    </div>
  );
}

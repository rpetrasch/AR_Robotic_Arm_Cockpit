"use client";

import { Button, Card, CardBody, CardHeader } from "@heroui/react";
import { viewerUrl, type Viewer } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";

const ICONS: Record<Viewer["id"], string> = {
  lichtblick_web: "bi-box",
  lichtblick: "bi-pc-display",
  novnc: "bi-display",
  native: "bi-window-stack",
};

/** The "Viewers" card (docs/specs/spec_cockpit.md §9.3): large buttons and the WebSocket URL. */
export function Viewers({
  viewers,
  lichtblickOff,
  busy,
  onStartLichtblick,
}: {
  viewers: Viewer[];
  lichtblickOff: boolean;
  busy: boolean;
  onStartLichtblick: () => void;
}) {
  const { t } = useI18n();
  const ws = viewers.find((v) => v.id === "lichtblick");
  return (
    <Card data-testid="viewers-card">
      <CardHeader className="flex flex-col items-start gap-1">
        <span className="font-semibold">{t("env.viewersTitle")}</span>
        <span className="text-sm opacity-80">{t("env.viewersHint")}</span>
      </CardHeader>
      <CardBody className="flex flex-col gap-4 pt-0">
        <div className="grid gap-3 md:grid-cols-3">
          {viewers
            .filter((v) => v.kind !== "foxglove")
            .map((v) => (
              <div
                key={v.id}
                className={`flex flex-col gap-2 rounded-lg border border-default-200 p-3 ${v.id === "native" ? "md:col-span-3" : ""}`}
                data-testid={`viewer-card-${v.id}`}
              >
                <span className="flex items-center gap-2 font-medium">
                  <i className={`bi ${ICONS[v.id]} text-xl`} /> {v.label}
                </span>
                <span className="text-sm opacity-80">{v.hint}</span>
                {v.commands.map((c) => (
                  <CliLine key={c} command={c} />
                ))}
                {v.kind === "http" &&
                  (v.available ? (
                    <Button as="a" href={viewerUrl(v)} target="_blank" rel="noopener" color="primary" size="sm" data-testid={`open-${v.id}`}>
                      {t("env.open")}
                    </Button>
                  ) : v.id === "lichtblick_web" && lichtblickOff ? (
                    <Button size="sm" variant="flat" isDisabled={busy} onPress={onStartLichtblick} data-testid="start-lichtblick">
                      {t("env.startLichtblick")}
                    </Button>
                  ) : (
                    <Button size="sm" isDisabled>
                      {t("env.notRunning")}
                    </Button>
                  ))}
              </div>
            ))}
        </div>
        {ws && (
          <div className="flex flex-col gap-1">
            <span className="text-sm">{t("env.wsUrl")}</span>
            <CliLine command={viewerUrl(ws)} />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

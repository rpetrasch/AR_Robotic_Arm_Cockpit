"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Accordion, AccordionItem, Card, CardBody, CardHeader, Chip, Select, SelectItem, Spinner } from "@heroui/react";
import { getComponents, type Component, type HealthRow, type ModeRow } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";
import { HealthDot } from "./HealthDot";

/** Environment → Components (docs/specs/spec_cockpit.md §3.4, §9.2): health, diagram, catalogue. */
export function Components({ rows, modes, current }: { rows: HealthRow[]; modes: ModeRow[]; current: string | null }) {
  const { t } = useI18n();
  const [preview, setPreview] = useState<string | null>(null);
  const mode = preview ?? current ?? undefined;
  const catalogue = useQuery({
    queryKey: ["components", mode ?? ""],
    queryFn: () => getComponents(mode),
    refetchInterval: 4000,
  });
  const items = catalogue.data?.components ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Card data-testid="health-table">
        <CardHeader className="font-semibold">{t("env.health")}</CardHeader>
        <CardBody className="pt-0">
          <table className="w-full text-sm">
            <thead className="text-left opacity-70">
              <tr>
                <th className="py-1 pr-3 font-medium">{t("env.component")}</th>
                <th className="py-1 pr-3 font-medium">{t("env.status")}</th>
                <th className="py-1 font-medium">{t("env.detail")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-default-200" data-testid={`health-${r.id}`}>
                  <td className="py-1 pr-3 font-mono">{r.id}</td>
                  <td className="py-1 pr-3">
                    <span className="flex items-center gap-2">
                      <HealthDot status={r.status} />
                      {r.status}
                    </span>
                  </td>
                  <td className="py-1 opacity-80">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>

      <Card data-testid="diagram">
        <CardHeader className="flex flex-wrap items-center gap-3">
          <span className="font-semibold">{t("env.catalogue")}</span>
          <Select
            aria-label={t("env.diagramFor")}
            label={t("env.diagramFor")}
            size="sm"
            className="max-w-xs"
            selectedKeys={mode ? [mode] : []}
            onSelectionChange={(keys) => setPreview((Array.from(keys)[0] as string) ?? null)}
            data-testid="diagram-mode"
          >
            {modes.map((m) => (
              <SelectItem key={m.id}>{`${m.chip} · ${m.command}`}</SelectItem>
            ))}
          </Select>
          <span className="text-sm opacity-70">{t("env.diagramHint")}</span>
        </CardHeader>
        <CardBody className="overflow-x-auto pt-0">
          {catalogue.isLoading ? <Spinner /> : <Diagram items={items} />}
        </CardBody>
      </Card>

      <Accordion variant="splitted" selectionMode="multiple" data-testid="catalogue">
        {items.map((c) => (
          <AccordionItem
            key={c.id}
            aria-label={c.name}
            data-testid={`component-${c.id}`}
            title={
              <span className="flex items-center gap-2">
                {c.live ? <HealthDot status={c.live.status} detail={c.live.detail} /> : <span className="w-2.5" />}
                <span className="font-medium">{c.name}</span>
                <Chip size="sm" variant="flat" color={c.in_mode ? "primary" : "default"}>
                  {c.in_mode ? t("env.inMode") : t("env.notInMode")}
                </Chip>
              </span>
            }
          >
            <ComponentCard c={c} />
          </AccordionItem>
        ))}
      </Accordion>
    </div>
  );
}

function ComponentCard({ c }: { c: Component }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-2 pb-2 text-sm">
      <p>{c.what}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-1 opacity-80">
        <span>
          {t("env.runsIn")}: {c.runs_in.join(", ")}
        </span>
        <span>
          {t("env.ports")}: {c.ports.join(", ") || "—"}
        </span>
        <span>
          {t("env.dependsOn")}: {c.depends.join(", ") || "—"}
        </span>
        <span>
          {t("env.docs")}: <code>{c.docs}</code>
        </span>
      </div>
      {c.live && (
        <p className="flex items-center gap-2">
          <HealthDot status={c.live.status} /> {c.live.status}: {c.live.detail}
        </p>
      )}
      <p className="font-medium">{t("env.cli")}</p>
      <div className="grid gap-1 md:grid-cols-[5rem_1fr]">
        {(["start", "stop", "logs", "status", "shell"] as const).map((k) => (
          <div key={k} className="contents">
            <span className="self-center opacity-70">{k}</span>
            <CliLine command={c.cli[k]} />
          </div>
        ))}
      </div>
    </div>
  );
}

const LAYERS = ["browser", "host", "stack", "infra"] as const;
const COL_W = 210;
const BOX_W = 170;
const BOX_H = 34;
const ROW_H = 48;
const TOP = 30;

/** Components by layer (columns) with their links; those of the selected mode highlighted. */
function Diagram({ items }: { items: Component[] }) {
  const { t } = useI18n();
  const pos = new Map<string, { x: number; y: number }>();
  LAYERS.forEach((layer, col) => {
    items
      .filter((c) => c.layer === layer)
      .forEach((c, row) => pos.set(c.id, { x: 10 + col * COL_W, y: TOP + row * ROW_H }));
  });
  const rows = Math.max(...LAYERS.map((l) => items.filter((c) => c.layer === l).length), 1);
  const height = TOP + rows * ROW_H;
  const edges = items.flatMap((c) =>
    c.links.filter((l) => pos.has(l.to)).map((l) => ({ from: c, to: items.find((i) => i.id === l.to)!, label: l.label })),
  );
  const byId = new Map(items.map((c) => [c.id, c]));
  return (
    <svg width={10 + LAYERS.length * COL_W} height={height} role="img" aria-label={t("env.catalogue")} className="text-xs">
      {LAYERS.map((layer, col) => (
        <text key={layer} x={10 + col * COL_W} y={14} className="fill-default-500 font-semibold uppercase">
          {layer}
        </text>
      ))}
      {edges.map(({ from, to, label }) => {
        const a = pos.get(from.id)!;
        const b = pos.get(to.id)!;
        const active = from.in_mode && to.in_mode;
        const sameCol = a.x === b.x;
        const x1 = sameCol ? a.x + BOX_W : a.x < b.x ? a.x + BOX_W : a.x;
        const x2 = sameCol ? b.x + BOX_W : a.x < b.x ? b.x : b.x + BOX_W;
        const y1 = a.y + BOX_H / 2;
        const y2 = b.y + BOX_H / 2;
        const bend = sameCol ? 30 : (x2 - x1) / 2;
        const d = `M${x1},${y1} C${x1 + bend},${y1} ${x2 - (sameCol ? -30 : bend)},${y2} ${x2},${y2}`;
        return (
          <g key={`${from.id}-${to.id}`} opacity={active ? 1 : 0.25}>
            <path d={d} fill="none" className="stroke-primary" strokeWidth={1.4} />
            <text x={(x1 + x2) / 2 + (sameCol ? 34 : 0)} y={(y1 + y2) / 2 - 3} textAnchor="middle" className="fill-default-600">
              {label}
            </text>
          </g>
        );
      })}
      {[...pos.entries()].map(([id, p]) => {
        const c = byId.get(id)!;
        const status = c.live?.status;
        return (
          <g key={id} opacity={c.in_mode ? 1 : 0.35} data-testid={`node-${id}`} data-in-mode={c.in_mode}>
            <rect
              x={p.x}
              y={p.y}
              width={BOX_W}
              height={BOX_H}
              rx={8}
              className={c.in_mode ? "fill-primary-50 stroke-primary" : "fill-default-100 stroke-default-300"}
            />
            {status && (
              <circle
                cx={p.x + 12}
                cy={p.y + BOX_H / 2}
                r={4}
                className={
                  { ready: "fill-success", waiting: "fill-warning", warn: "fill-warning", error: "fill-danger", off: "fill-default-300", unknown: "fill-default-400", down: "fill-danger" }[status]
                }
              />
            )}
            <text x={p.x + 22} y={p.y + BOX_H / 2 + 4} className="fill-foreground">
              {c.name.length > 24 ? `${c.name.slice(0, 23)}…` : c.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

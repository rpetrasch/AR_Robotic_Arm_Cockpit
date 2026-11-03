"use client";

import { useRef, useState } from "react";
import { Button, Input, Select, SelectItem, Switch } from "@heroui/react";
import type { SiteContext } from "@/lib/api/scenarios";
import { useI18n } from "@/lib/i18n";
import type { SceneObject, Scenario } from "./doc";

const W = 640; // px of the table's length
const COLORS: Record<string, string> = { red: "#d64545", green: "#2f9e44", blue: "#3366cc", yellow: "#e0b000", orange: "#e8590c", white: "#e9ecef", black: "#343a40", grey: "#868e96" };
const PALETTE: { label: string; obj: Omit<SceneObject, "id" | "pose">; list: "objects" | "obstacles" }[] = [
  { label: "box", obj: { shape: "box", size: [0.03, 0.03, 0.03], color: "red", spawn: true }, list: "objects" },
  { label: "cylinder", obj: { shape: "cylinder", size: [0.03, 0.05], color: "green", spawn: true }, list: "objects" },
  { label: "sphere", obj: { shape: "sphere", size: [0.04], color: "blue", spawn: true }, list: "objects" },
  { label: "obstacle", obj: { shape: "box", size: [0.1, 0.05, 0.15], color: "grey", spawn: false }, list: "obstacles" },
];

function fill(c: SceneObject["color"]): string {
  if (Array.isArray(c)) return `rgb(${c.slice(0, 3).map((v) => Math.round(v * 255)).join(",")})`;
  return COLORS[c ?? "grey"] ?? COLORS.grey;
}

function footprint(o: SceneObject): [number, number] {
  return o.shape === "box" ? [o.size[0], o.size[1] ?? o.size[0]] : [o.size[0], o.size[0]];
}

/**
 * Top-down table view (docs/specs/spec_cockpit.md §12.3 scene editor): drag objects, rotate with the
 * handle, add from the palette. Bins from config/bins.yaml, the arm's base and its approximate
 * reach ring, and the move_pose targets (numbered by step) are drawn for orientation.
 */
export function SceneEditor({
  data,
  site,
  onChange,
}: {
  data: Scenario;
  site: SiteContext;
  onChange: (list: "objects" | "obstacles", index: number, value: SceneObject | undefined) => void;
}) {
  const { t } = useI18n();
  const svg = useRef<SVGSVGElement>(null);
  const [sel, setSel] = useState<{ list: "objects" | "obstacles"; i: number } | null>(null);
  const [drag, setDrag] = useState<{ mode: "move" | "rotate"; list: "objects" | "obstacles"; i: number } | null>(null);
  const [lx, ly] = site.table.size;
  const k = W / lx;
  const H = ly * k;
  const px = (x: number) => W / 2 + x * k;
  const py = (y: number) => H / 2 - y * k;
  const toTable = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const sx = ((e.clientX - r.left) / r.width) * W;
    const sy = ((e.clientY - r.top) / r.height) * H;
    return { x: (sx - W / 2) / k, y: (H / 2 - sy) / k };
  };
  const lists = { objects: data.scene?.objects ?? [], obstacles: data.scene?.obstacles ?? [] };
  const selected = sel ? lists[sel.list][sel.i] : undefined;
  const round = (v: number, d = 3) => Math.round(v * 10 ** d) / 10 ** d;

  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const o = lists[drag.list][drag.i];
    const p = toTable(e);
    if (drag.mode === "move") {
      onChange(drag.list, drag.i, { ...o, pose: { ...o.pose, x: round(p.x), y: round(p.y) } });
    } else {
      const yaw = (Math.atan2(p.y - o.pose.y, p.x - o.pose.x) * 180) / Math.PI;
      onChange(drag.list, drag.i, { ...o, pose: { ...o.pose, yaw_deg: round(yaw, 1) } });
    }
  };
  const add = (item: (typeof PALETTE)[number]) => {
    const ids = new Set([...lists.objects, ...lists.obstacles].map((o) => o.id));
    let n = 1;
    while (ids.has(`${item.label}_${n}`)) n++;
    const i = lists[item.list].length;
    onChange(item.list, i, { id: `${item.label}_${n}`, ...item.obj, pose: { x: 0.2, y: -0.2 + 0.05 * (i % 6) } } as SceneObject);
    setSel({ list: item.list, i });
  };
  const targets = (data.task?.steps ?? [])
    .map((s, i) => ("move_pose" in s && (s.move_pose.frame ?? "table") === "table" ? { i, ...s.move_pose } : null))
    .filter(Boolean) as { i: number; x: number; y: number; z: number }[];
  const base = site.mount.xyz;

  return (
    <div className="flex flex-col gap-3 xl:flex-row" data-testid="scene-editor">
      <svg
        ref={svg}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-w-[640px] touch-none select-none rounded-lg border border-default-200"
        onPointerMove={onMove}
        onPointerUp={() => setDrag(null)}
        onPointerLeave={() => setDrag(null)}
        role="img"
        aria-label={t("sim.scene")}
      >
        <rect x={0} y={0} width={W} height={H} className="fill-amber-100 dark:fill-amber-950" />
        <circle cx={px(base[0])} cy={py(base[1])} r={site.reach.radius_m * k} fill="none" strokeDasharray="6 4" className="stroke-primary" opacity={0.6} />
        <circle cx={px(base[0])} cy={py(base[1])} r={0.06 * k} className="fill-default-400" />
        <text x={px(base[0])} y={py(base[1]) + 4} textAnchor="middle" className="fill-background text-[10px]">
          base
        </text>
        {site.bins.map((b) => (
          <g key={b.name} transform={`translate(${px(b.xyz[0])} ${py(b.xyz[1])}) rotate(${(-(b.yaw ?? 0) * 180) / Math.PI})`}>
            <rect x={(-b.size[0] / 2) * k} y={(-b.size[1] / 2) * k} width={b.size[0] * k} height={b.size[1] * k} fill="none" strokeWidth={3}
              stroke={b.color ? `rgb(${b.color.slice(0, 3).map((v) => Math.round(v * 255)).join(",")})` : "#888"} />
            <text y={4} textAnchor="middle" className="fill-default-600 text-[10px]">
              {b.name}
            </text>
          </g>
        ))}
        {targets.map((tg) => (
          <g key={tg.i} transform={`translate(${px(tg.x)} ${py(tg.y)})`} data-testid={`target-${tg.i}`}>
            <path d="M-7,0 H7 M0,-7 V7" className="stroke-foreground" strokeWidth={1.5} />
            <text x={8} y={-6} className="fill-foreground text-[10px]">
              {tg.i + 1} (z {tg.z})
            </text>
          </g>
        ))}
        {(["objects", "obstacles"] as const).flatMap((list) =>
          lists[list].map((o, i) => {
            const [fx, fy] = footprint(o);
            const isSel = sel?.list === list && sel.i === i;
            const yaw = o.pose.yaw_deg ?? 0;
            return (
              <g key={`${list}-${o.id}-${i}`} transform={`translate(${px(o.pose.x)} ${py(o.pose.y)}) rotate(${-yaw})`} data-testid={`obj-${o.id}`}>
                {o.shape === "box" || o.shape === "mesh" ? (
                  <rect x={(-fx / 2) * k} y={(-fy / 2) * k} width={fx * k} height={fy * k} fill={fill(o.color)}
                    strokeWidth={isSel ? 3 : 1} className={isSel ? "stroke-primary" : "stroke-foreground"} strokeDasharray={list === "obstacles" ? "4 2" : undefined}
                    onPointerDown={(e) => (e.currentTarget.ownerSVGElement?.setPointerCapture?.(e.pointerId), setSel({ list, i }), setDrag({ mode: "move", list, i }))} style={{ cursor: "move" }} />
                ) : (
                  <circle r={(fx / 2) * k} fill={fill(o.color)} strokeWidth={isSel ? 3 : 1} className={isSel ? "stroke-primary" : "stroke-foreground"}
                    onPointerDown={() => (setSel({ list, i }), setDrag({ mode: "move", list, i }))} style={{ cursor: "move" }} />
                )}
                {isSel && (
                  <>
                    <line x1={0} y1={0} x2={(fx / 2) * k + 14} y2={0} className="stroke-primary" />
                    <circle cx={(fx / 2) * k + 14} cy={0} r={5} className="fill-primary" style={{ cursor: "grab" }} data-testid="rotate-handle"
                      onPointerDown={(e) => (e.stopPropagation(), setDrag({ mode: "rotate", list, i }))} />
                  </>
                )}
              </g>
            );
          }),
        )}
      </svg>
      <div className="flex min-w-64 flex-col gap-2 text-sm">
        <span className="font-medium">{t("sim.palette")}</span>
        <div className="flex flex-wrap gap-2">
          {PALETTE.map((p) => (
            <Button key={p.label} size="sm" variant="flat" onPress={() => add(p)} data-testid={`add-${p.label}`}>
              + {p.label}
            </Button>
          ))}
        </div>
        <p className="text-xs opacity-70">{t("sim.sceneHint", { r: site.reach.radius_m })}</p>
        {selected && sel && (
          <div className="flex flex-col gap-2 rounded-lg border border-default-200 p-2" data-testid="object-props">
            <Input size="sm" label="id" value={selected.id} onValueChange={(v) => onChange(sel.list, sel.i, { ...selected, id: v.replace(/[^a-z0-9_]/g, "") })} />
            <Select size="sm" label={t("sim.shape")} selectedKeys={[selected.shape]} onSelectionChange={(key) => {
              const shape = Array.from(key)[0] as SceneObject["shape"];
              const size = shape === "box" ? [0.03, 0.03, 0.03] : shape === "cylinder" ? [0.03, 0.05] : [0.04];
              onChange(sel.list, sel.i, { ...selected, shape, size });
            }}>
              {["box", "cylinder", "sphere"].map((s) => <SelectItem key={s}>{s}</SelectItem>)}
            </Select>
            <Input size="sm" label={t("sim.size")} value={selected.size.join(", ")}
              onValueChange={(v) => {
                const nums = v.split(",").map((x) => Number(x.trim())).filter((x) => x > 0);
                if (nums.length) onChange(sel.list, sel.i, { ...selected, size: nums });
              }} />
            <div className="flex gap-2">
              {(["x", "y", "yaw_deg"] as const).map((f) => (
                <Input key={f} size="sm" type="number" step="0.005" label={f} value={String(selected.pose[f] ?? 0)}
                  onValueChange={(v) => onChange(sel.list, sel.i, { ...selected, pose: { ...selected.pose, [f]: Number(v) } })} />
              ))}
            </div>
            <Select size="sm" label={t("sim.color")} selectedKeys={[typeof selected.color === "string" ? selected.color : "grey"]}
              onSelectionChange={(key) => onChange(sel.list, sel.i, { ...selected, color: Array.from(key)[0] as string })}>
              {Object.keys(COLORS).map((c) => <SelectItem key={c}>{c}</SelectItem>)}
            </Select>
            <Switch size="sm" isSelected={!!selected.spawn} onValueChange={(v) => onChange(sel.list, sel.i, { ...selected, spawn: v })}>
              {t("sim.spawn")}
            </Switch>
            <Button size="sm" color="danger" variant="flat" onPress={() => (onChange(sel.list, sel.i, undefined), setSel(null))} data-testid="remove-object">
              {t("sim.removeObject")}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

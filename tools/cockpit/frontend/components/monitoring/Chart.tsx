"use client";

import { useEffect, useRef } from "react";
import uPlot, { type AlignedData, type Options } from "uplot";
import "uplot/dist/uPlot.min.css";

/**
 * Categorical series colours in fixed order (never cycled): the validated reference palette of
 * the dataviz guidance, light and dark steps. Joint i always gets slot i, so a hidden joint
 * does not repaint the others.
 */
const LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];

function dark(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.classList.contains("dark");
}

export function seriesColor(slot: number): string {
  const palette = dark() ? DARK : LIGHT;
  return palette[slot % palette.length];
}

export interface Series {
  label: string;
  slot: number;
  dashed?: boolean;
  show?: boolean;
}

/**
 * A uPlot time chart: x in epoch seconds, one y axis (one unit per chart), a crosshair with the
 * values in the legend on hover. `data` changes are applied with setData, not a rebuild.
 */
export function Chart({
  data,
  series,
  unit,
  height = 200,
  testId,
}: {
  data: AlignedData;
  series: Series[];
  unit: string;
  height?: number;
  testId?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const key = series.map((s) => `${s.label}:${s.slot}:${s.dashed ?? false}`).join("|") + unit;

  useEffect(() => {
    if (!box.current) return;
    const ink = dark() ? "#c3c2b7" : "#52514e";
    const grid = dark() ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.07)";
    const opts: Options = {
      width: box.current.clientWidth || 600,
      height,
      cursor: { drag: { x: false, y: false }, points: { size: 8 } },
      legend: { live: true },
      scales: { x: { time: true } },
      axes: [
        { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } },
        {
          stroke: ink,
          grid: { stroke: grid, width: 1 },
          ticks: { stroke: grid },
          size: 56,
          values: (_u, vals) => vals.map((v) => `${+v.toFixed(3)}`),
          label: unit,
        },
      ],
      series: [
        { label: "time" },
        ...series.map((s) => ({
          label: s.label,
          stroke: seriesColor(s.slot),
          width: 2,
          dash: s.dashed ? [6, 4] : undefined,
          show: s.show ?? true,
          points: { show: false },
          value: (_u: uPlot, v: number | null) => (v == null ? "—" : `${v.toFixed(4)} ${unit}`),
        })),
      ],
    };
    plot.current = new uPlot(opts, data, box.current);
    const ro = new ResizeObserver(() => {
      if (box.current && plot.current) plot.current.setSize({ width: box.current.clientWidth, height });
    });
    ro.observe(box.current);
    return () => {
      ro.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
    // rebuild only when the series layout changes; data goes through setData below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, height]);

  useEffect(() => {
    plot.current?.setData(data);
  }, [data]);

  return <div ref={box} className="w-full text-xs" data-testid={testId} data-points={data[0]?.length ?? 0} />;
}

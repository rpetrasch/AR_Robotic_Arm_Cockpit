"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";
import Anser from "anser";
import { Button, Input, Switch } from "@heroui/react";
import type { JobLine } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";

/** "/regex/" searches as a regular expression, anything else as plain text. */
function matcher(query: string): ((s: string) => boolean) | null {
  if (!query) return null;
  const re = query.match(/^\/(.+)\/$/);
  if (re) {
    try {
      const r = new RegExp(re[1], "i");
      return (s) => r.test(s);
    } catch {
      return () => false;
    }
  }
  const q = query.toLowerCase();
  return (s) => s.toLowerCase().includes(q);
}

/** ROS and launch print `[ERROR]` / `[WARN]` without ANSI colours when stdout is not a terminal. */
export function levelOf(text: string): "error" | "warn" | null {
  const m = text.match(/\[(FATAL|ERROR|WARN)\]/);
  return m ? (m[1] === "WARN" ? "warn" : "error") : null;
}

function levelClass(text: string): string {
  const level = levelOf(text);
  return level === "warn" ? "text-amber-300" : level === "error" ? "text-red-400" : "";
}

function Ansi({ text }: { text: string }) {
  const parts = useMemo(() => Anser.ansiToJson(text, { use_classes: false, remove_empty: true }), [text]);
  return (
    <>
      {parts.map((p, i) => (
        <span
          key={i}
          style={{
            color: p.fg ? `rgb(${p.fg})` : undefined,
            backgroundColor: p.bg ? `rgb(${p.bg})` : undefined,
            fontWeight: p.decorations.includes("bold") ? 600 : undefined,
          }}
        >
          {p.content}
        </span>
      ))}
    </>
  );
}

/**
 * The live log of a job (docs/specs/spec_cockpit.md §8.3): virtualised, ANSI colours, error lines
 * highlighted, follow mode, search, copy and download.
 */
export function JobLog({
  lines,
  downloadUrl,
  focusSeq,
  levelsOnly: levelsOnlyProp,
  onLevelsOnly,
  fill,
  onAskLine,
}: {
  lines: JobLine[];
  downloadUrl: string;
  focusSeq?: number | null;
  /** Show only warning and error lines; controlled when the page offers its own button. */
  levelsOnly?: boolean;
  onLevelsOnly?: (on: boolean) => void;
  /** Take all the height of the parent (a flex column with a definite height). */
  fill?: boolean;
  /** right-click on a line: "Ask the assistant about this line" (§14.1) */
  onAskLine?: (line: JobLine) => void;
}) {
  const { t } = useI18n();
  const [menu, setMenu] = useState<{ x: number; y: number; line: JobLine } | null>(null);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menu]);
  const [levelsLocal, setLevelsLocal] = useState(false);
  const levelsOnly = levelsOnlyProp ?? levelsLocal;
  const setLevelsOnly = onLevelsOnly ?? setLevelsLocal;
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const [onlyMatches, setOnlyMatches] = useState(false);
  const list = useRef<VirtuosoHandle>(null);
  const test = matcher(query);
  const shown = lines.filter(
    (l) => (!onlyMatches || l.match) && (!levelsOnly || l.match || levelOf(l.text)) && (!test || test(l.text)),
  );

  useEffect(() => {
    if (focusSeq == null) return;
    setFollow(false);
    const index = shown.findIndex((l) => l.seq === focusSeq);
    if (index >= 0) list.current?.scrollToIndex({ index, align: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- jump once per click
  }, [focusSeq]);

  const copy = () => navigator.clipboard?.writeText(lines.map((l) => l.text).join("\n")).catch(() => {});

  return (
    <div className={`flex flex-col gap-2 ${fill ? "h-full min-h-0" : ""}`} data-testid="job-log">
      <div className="flex flex-wrap items-center gap-3">
        <Input size="sm" className="max-w-sm" placeholder={t("job.search")} value={query} onValueChange={setQuery} isClearable onClear={() => setQuery("")} startContent={<i className="bi bi-search" />} aria-label={t("job.search")} />
        <Switch size="sm" isSelected={onlyMatches} onValueChange={setOnlyMatches}>{t("job.onlyMatches")}</Switch>
        <Switch size="sm" isSelected={levelsOnly} onValueChange={setLevelsOnly} data-testid="job-levels-only">{t("job.levelsOnly")}</Switch>
        <Switch size="sm" isSelected={follow} onValueChange={setFollow} data-testid="job-follow">{t("job.follow")}</Switch>
        <div className="flex-1" />
        <Button size="sm" variant="light" startContent={<i className="bi bi-clipboard" />} onPress={copy}>{t("job.copy")}</Button>
        <Button size="sm" variant="light" as="a" href={downloadUrl} startContent={<i className="bi bi-download" />}>{t("job.download")}</Button>
      </div>
      <div className={`cli ${fill ? "min-h-0 flex-1" : "h-[60vh]"} rounded-lg border border-default-200 bg-neutral-950 text-neutral-100`}>
        <Virtuoso
          ref={list}
          data={shown}
          followOutput={follow ? "auto" : false}
          initialTopMostItemIndex={Math.max(shown.length - 1, 0)}
          itemContent={(_i, l) => (
            <div
              className={`flex gap-3 whitespace-pre-wrap break-all px-3 leading-5 ${
                l.match ? "bg-red-900/50" : ""
              } ${l.stream === "err" ? "text-red-300" : l.stream === "cmd" ? "text-sky-300" : levelClass(l.text)}`}
              data-seq={l.seq}
              data-match={l.match ?? undefined}
              onContextMenu={
                onAskLine
                  ? (e) => {
                      e.preventDefault();
                      setMenu({ x: e.clientX, y: e.clientY, line: l });
                    }
                  : undefined
              }
            >
              <span className="shrink-0 select-none text-neutral-500">{l.ts.slice(11, 19)}</span>
              <span className="min-w-0">
                <Ansi text={l.text} />
              </span>
            </div>
          )}
        />
      </div>
      {menu && onAskLine && (
        <div className="fixed z-50 rounded-md border border-default-200 bg-content1 p-1 shadow-lg" style={{ left: menu.x, top: menu.y }} data-testid="log-menu">
          <button type="button" className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-default-100" onClick={() => { onAskLine(menu.line); setMenu(null); }} data-testid="log-menu-ask">
            <i className="bi bi-stars text-secondary" /> {t("job.askLine")}
          </button>
        </div>
      )}
    </div>
  );
}

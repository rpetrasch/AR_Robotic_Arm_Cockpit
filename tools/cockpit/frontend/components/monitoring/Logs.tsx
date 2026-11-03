"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";
import Anser from "anser";
import { Button, Card, CardBody, Chip, Input, Select, SelectItem, Switch } from "@heroui/react";
import { getLogSources, getLogs, LEVELS, logsDownloadUrl, logsSocketPath, type LogFilter, type LogLine } from "@/lib/api/monitoring";
import { useSocket } from "@/lib/api/socket";
import { useI18n } from "@/lib/i18n";

const KEEP = 5000;
const LEVEL_COLOR: Record<string, string> = {
  DEBUG: "text-default-400",
  INFO: "text-default-600",
  WARN: "text-warning",
  ERROR: "text-danger",
  FATAL: "text-danger font-bold",
};
const RANGES = { "": "any time", "15m": "last 15 min", "1h": "last hour", "24h": "last 24 h", "7d": "last 7 days" } as const;
const RANGE_S: Record<string, number> = { "15m": 900, "1h": 3600, "24h": 86400, "7d": 604800 };

function Text({ text }: { text: string }) {
  const parts = useMemo(() => Anser.ansiToJson(text, { use_classes: false, remove_empty: true }), [text]);
  return (
    <>
      {parts.map((p, i) => (
        <span key={i} style={p.fg ? { color: `rgb(${p.fg})` } : undefined}>
          {p.content}
        </span>
      ))}
    </>
  );
}

/** Monitoring → Logs (docs/specs/spec_cockpit.md §10.2): filters, search, live tail, download. */
export function Logs() {
  const { t } = useI18n();
  const [source, setSource] = useState("");
  const [level, setLevel] = useState("");
  const [node, setNode] = useState("");
  const [q, setQ] = useState("");
  const [regex, setRegex] = useState(false);
  const [range, setRange] = useState("");
  const [live, setLive] = useState(true);
  const [lines, setLines] = useState<LogLine[]>([]);
  const list = useRef<VirtuosoHandle>(null);
  const paused = useRef(!live);
  paused.current = !live;
  const pending = useRef<LogLine[]>([]);

  const filter: LogFilter = useMemo(() => {
    const from = range ? new Date(Date.now() - RANGE_S[range] * 1000).toISOString() : undefined;
    return { source: source || undefined, level: level || undefined, node: node || undefined, q: q || undefined, regex, from };
  }, [source, level, node, q, regex, range]);
  const key = JSON.stringify({ ...filter, from: range });

  const sources = useQuery({ queryKey: ["log-sources"], queryFn: getLogSources, refetchInterval: 10000 });
  const search = useQuery({ queryKey: ["logs", key], queryFn: () => getLogs(filter, 1000), retry: false });
  useEffect(() => {
    if (search.data) setLines([...search.data.lines].reverse());
  }, [search.data]);

  useSocket(logsSocketPath({ ...filter, from: undefined }), (msg) => {
    if (msg.type !== "lines") return;
    if (paused.current) {
      pending.current.push(...msg.lines);
      return;
    }
    setLines((old) => [...old, ...msg.lines].slice(-KEEP));
  });
  useEffect(() => {
    if (live && pending.current.length) {
      const add = pending.current;
      pending.current = [];
      setLines((old) => [...old, ...add].slice(-KEEP));
    }
  }, [live]);

  const groups = useMemo(() => {
    const all = (sources.data ?? []).map((s) => s.source);
    const prefixes = Array.from(new Set(all.filter((s) => s.includes(":")).map((s) => `${s.split(":")[0]}:`)));
    return [...prefixes, ...all];
  }, [sources.data]);

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <CardBody className="flex flex-row flex-wrap items-end gap-3">
          <Select
            label={t("logs.source")}
            size="sm"
            className="w-64"
            selectedKeys={source ? [source] : ["__all"]}
            onSelectionChange={(k) => {
              const v = Array.from(k)[0] as string;
              setSource(v === "__all" ? "" : (v ?? ""));
            }}
            data-testid="log-source"
          >
            {[
              <SelectItem key="__all">{t("logs.allSources")}</SelectItem>,
              ...groups.map((g) => <SelectItem key={g}>{g.endsWith(":") ? `${g} *` : g}</SelectItem>),
            ]}
          </Select>
          <Select
            label={t("logs.level")}
            size="sm"
            className="w-36"
            selectedKeys={level ? [level] : ["__all"]}
            onSelectionChange={(k) => {
              const v = Array.from(k)[0] as string;
              setLevel(v === "__all" ? "" : (v ?? ""));
            }}
            data-testid="log-level"
          >
            {[<SelectItem key="__all">{t("logs.anyLevel")}</SelectItem>, ...LEVELS.map((l) => <SelectItem key={l}>{`≥ ${l}`}</SelectItem>)]}
          </Select>
          <Input label={t("logs.node")} size="sm" className="w-40" value={node} onValueChange={setNode} data-testid="log-node" />
          <Input label={t("logs.search")} size="sm" className="w-72" value={q} onValueChange={setQ} data-testid="log-search" />
          <Switch size="sm" isSelected={regex} onValueChange={setRegex}>
            regex
          </Switch>
          <Select label={t("logs.range")} size="sm" className="w-40" selectedKeys={[range || "__any"]} onSelectionChange={(k) => {
            const v = Array.from(k)[0] as string;
            setRange(v === "__any" ? "" : v);
          }}>
            {Object.entries(RANGES).map(([k, v]) => (
              <SelectItem key={k || "__any"}>{v}</SelectItem>
            ))}
          </Select>
          <Switch size="sm" isSelected={live} onValueChange={setLive} data-testid="log-live">
            {live ? t("logs.live") : t("logs.paused", { n: pending.current.length })}
          </Switch>
          <Button as="a" href={logsDownloadUrl(filter)} size="sm" variant="flat" data-testid="log-download">
            <i className="bi bi-download" /> {t("logs.download")}
          </Button>
        </CardBody>
      </Card>
      {search.error && (
        <p className="text-sm text-danger" role="alert">
          {(search.error as Error).message}
        </p>
      )}
      <div className="flex items-center gap-2 text-xs opacity-70">
        <Chip size="sm" variant="flat" data-testid="log-count">
          {t("logs.count", { n: lines.length })}
        </Chip>
        {search.data?.more && <span>{t("logs.more")}</span>}
        <span>{t("logs.hint")}</span>
      </div>
      <div className="h-[60vh] rounded-lg border border-default-200 bg-content1 font-mono text-xs" data-testid="log-lines">
        <Virtuoso
          ref={list}
          data={lines}
          followOutput={live ? "auto" : false}
          initialTopMostItemIndex={Math.max(0, lines.length - 1)}
          itemContent={(_i, l) => (
            <div className="flex gap-2 px-2 py-0.5 hover:bg-default-100" data-testid="log-line" data-level={l.level}>
              <span className="shrink-0 opacity-60">{new Date(l.time).toLocaleTimeString([], { hour12: false })}</span>
              <span className={`w-12 shrink-0 ${LEVEL_COLOR[l.level]}`}>{l.level}</span>
              <span className="w-40 shrink-0 truncate opacity-70" title={l.source}>
                {l.source}
              </span>
              {l.node && <span className="shrink-0 text-primary">{l.node}</span>}
              <span className="whitespace-pre-wrap break-all">
                <Text text={l.text} />
              </span>
            </div>
          )}
        />
      </div>
    </div>
  );
}

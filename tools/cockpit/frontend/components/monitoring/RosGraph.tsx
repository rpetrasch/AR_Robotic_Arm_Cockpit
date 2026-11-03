"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, Input, Spinner, Tab, Tabs } from "@heroui/react";
import { getGraph, getNodeParams, measureRate, type GraphTopic } from "@/lib/api/monitoring";
import { useI18n } from "@/lib/i18n";

/** Monitoring → ROS Graph (docs/specs/spec_cockpit.md §10.4). */
export function RosGraph({ running }: { running: boolean }) {
  const { t } = useI18n();
  const graph = useQuery({ queryKey: ["ros-graph"], queryFn: getGraph, enabled: running, refetchInterval: 10000, retry: false });
  const [q, setQ] = useState("");
  const [node, setNode] = useState<string | null>(null);
  const [rates, setRates] = useState<Record<string, number>>({});
  const measure = useMutation({ mutationFn: (name: string) => measureRate(name), onSuccess: (r) => setRates((x) => ({ ...x, [r.topic]: r.hz })) });
  const params = useQuery({ queryKey: ["params", node], queryFn: () => getNodeParams(node!), enabled: !!node, retry: false });

  const match = (s: string) => s.toLowerCase().includes(q.toLowerCase());
  const g = graph.data;
  const nodes = (g?.nodes ?? []).filter(match);
  const topics = (g?.topics ?? []).filter((x) => match(x.name) || x.types.some(match));
  const forNode = useMemo(() => {
    if (!g || !node) return { pub: [] as GraphTopic[], sub: [] as GraphTopic[] };
    return { pub: g.topics.filter((x) => x.publishers.includes(node)), sub: g.topics.filter((x) => x.subscribers.includes(node)) };
  }, [g, node]);

  if (!running) return <p className="text-sm opacity-80">{t("tel.noStack")}</p>;
  if (graph.isLoading) return <Spinner />;
  if (graph.error) return <p className="text-sm text-danger">{(graph.error as Error).message}</p>;
  return (
    <div className="flex flex-col gap-3">
      <Input size="sm" className="max-w-sm" label={t("graph.search")} value={q} onValueChange={setQ} data-testid="graph-search" />
      <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
        <Card>
          <CardHeader className="font-semibold">{t("graph.nodes", { n: nodes.length })}</CardHeader>
          <CardBody className="max-h-[60vh] overflow-auto pt-0 text-sm">
            {nodes.map((n) => (
              <button
                key={n}
                className={`block w-full rounded px-2 py-0.5 text-left font-mono hover:bg-default-100 ${n === node ? "bg-primary-50 text-primary" : ""}`}
                onClick={() => setNode(n)}
                data-testid={`node-${n}`}
              >
                {n}
              </button>
            ))}
          </CardBody>
        </Card>
        {node ? (
          <Card data-testid="node-detail">
            <CardHeader className="flex items-center gap-2 font-semibold">
              <span className="font-mono">{node}</span>
              <Button size="sm" variant="light" className="ml-auto" onPress={() => setNode(null)}>
                {t("common.close")}
              </Button>
            </CardHeader>
            <CardBody className="gap-3 pt-0 text-sm">
              <NodeGraph node={node} pub={forNode.pub} sub={forNode.sub} onPick={setQ} />
              <p className="font-medium">{t("graph.params")}</p>
              {params.isLoading && <Spinner size="sm" />}
              {params.error && <p className="text-danger">{(params.error as Error).message}</p>}
              <div className="max-h-72 overflow-auto font-mono text-xs" data-testid="node-params">
                {(params.data?.params ?? []).map((p) => (
                  <div key={p.name} className="flex gap-2 border-t border-default-100 py-0.5">
                    <span className="w-1/2 shrink-0 truncate" title={p.name}>{p.name}</span>
                    <span className="break-all opacity-80">{p.unavailable ? t("graph.unavailable") : JSON.stringify(p.value)}</span>
                  </div>
                ))}
              </div>
              <p className="text-xs opacity-70">{t("graph.readOnly")}</p>
            </CardBody>
          </Card>
        ) : (
          <Card>
            <CardHeader className="font-semibold">{t("graph.topics", { n: topics.length })}</CardHeader>
            <CardBody className="max-h-[60vh] overflow-auto pt-0">
              <table className="w-full text-xs" data-testid="graph-topics">
                <thead className="text-left opacity-70">
                  <tr>
                    <th className="py-1 pr-2 font-medium">{t("graph.topic")}</th>
                    <th className="py-1 pr-2 font-medium">{t("graph.type")}</th>
                    <th className="py-1 pr-2 text-right font-medium">pub / sub</th>
                    <th className="py-1 text-right font-medium">{t("graph.rate")}</th>
                  </tr>
                </thead>
                <tbody>
                  {topics.map((x) => {
                    const hz = rates[x.name] ?? x.rate?.hz;
                    return (
                      <tr key={x.name} className="border-t border-default-200">
                        <td className="py-1 pr-2 font-mono">{x.name}</td>
                        <td className="py-1 pr-2 opacity-80">{x.types.join(", ")}</td>
                        <td className="py-1 pr-2 text-right" title={`pub: ${x.publishers.join(", ")}\nsub: ${x.subscribers.join(", ")}`}>
                          {x.publishers.length} / {x.subscribers.length}
                        </td>
                        <td className="py-1 text-right">
                          {hz !== undefined ? `${hz.toFixed(1)} Hz ` : ""}
                          <Button size="sm" variant="light" isIconOnly aria-label={t("graph.measure")} onPress={() => measure.mutate(x.name)} isLoading={measure.isPending && measure.variables === x.name}>
                            <i className="bi bi-speedometer2" />
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardBody>
          </Card>
        )}
      </div>
      {g && (
        <p className="text-xs opacity-70">
          {t("graph.summary", { services: g.services.length, actions: g.actions.length })} {g.actions.map((a) => a.name).join(", ")}
        </p>
      )}
    </div>
  );
}

/** Publishers → node → subscribers: the topics of one node, left the ones it subscribes. */
function NodeGraph({ node, pub, sub, onPick }: { node: string; pub: GraphTopic[]; sub: GraphTopic[]; onPick: (q: string) => void }) {
  const rows = Math.max(pub.length, sub.length, 1);
  const h = rows * 22 + 20;
  const cx = 300;
  return (
    <svg viewBox={`0 0 600 ${h}`} className="w-full text-[10px]" role="img" aria-label={node} data-testid="node-graph">
      <rect x={cx - 80} y={h / 2 - 14} width={160} height={28} rx={8} className="fill-primary-50 stroke-primary" />
      <text x={cx} y={h / 2 + 4} textAnchor="middle" className="fill-foreground font-mono">
        {node.length > 26 ? `${node.slice(0, 25)}…` : node}
      </text>
      {sub.map((x, i) => {
        const y = 14 + i * 22;
        return (
          <g key={`s${x.name}`} className="cursor-pointer" onClick={() => onPick(x.name)}>
            <path d={`M190,${y} C205,${y} 205,${h / 2} ${cx - 80},${h / 2}`} fill="none" className="stroke-default-400" />
            <text x={186} y={y + 3} textAnchor="end" className="fill-default-600 font-mono">{x.name}</text>
          </g>
        );
      })}
      {pub.map((x, i) => {
        const y = 14 + i * 22;
        return (
          <g key={`p${x.name}`} className="cursor-pointer" onClick={() => onPick(x.name)}>
            <path d={`M${cx + 80},${h / 2} C395,${h / 2} 395,${y} 410,${y}`} fill="none" className="stroke-primary" />
            <text x={414} y={y + 3} className="fill-default-600 font-mono">{x.name}</text>
          </g>
        );
      })}
    </svg>
  );
}

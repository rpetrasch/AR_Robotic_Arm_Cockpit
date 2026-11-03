"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, Chip, Select, SelectItem, Spinner, Textarea, Tooltip } from "@heroui/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ask,
  createThread,
  getAiModels,
  getThread,
  listThreads,
  updateThread,
  type AskContext,
  type Message,
  type Proposal,
  type ScenarioSuggestion,
  type Source,
  type StreamEvent,
  type Task,
} from "@/lib/api/assistant";
import { startJob } from "@/lib/api/cockpit";
import { ApiError } from "@/lib/api/http";
import { useAssistant } from "@/lib/assistant";
import { useI18n } from "@/lib/i18n";
import { CopyButton } from "@/components/common/CopyButton";
import { Markdown } from "@/components/assistant/Markdown";

interface Live {
  question: string;
  task: Task;
  text: string;
  reasoning: string;
  meta: Extract<StreamEvent, { event: "meta" }> | null;
  error: Extract<StreamEvent, { event: "error" }> | null;
}

/**
 * The assistant (docs/specs/spec_cockpit.md §14): a right-side drawer that survives navigation.
 * Threads per user; answers stream with numbered sources, a footer (provider, model, sources,
 * latency) and the reasoning collapsed. Proposed steps are buttons that start a job after a
 * confirmation; other commands are copyable text.
 */
export function AssistantDrawer({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const assistant = useAssistant();
  const [threadId, setThreadId] = useState<string | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [input, setInput] = useState("");
  const [showModel, setShowModel] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const threads = useQuery({ queryKey: ["assistant", "threads"], queryFn: listThreads });
  const thread = useQuery({ queryKey: ["assistant", "thread", threadId], queryFn: () => getThread(threadId!), enabled: !!threadId });

  // the newest thread when the drawer opens
  useEffect(() => {
    if (!threadId && !assistant.pending && threads.data?.length) setThreadId(threads.data[0].id);
  }, [threads.data, threadId, assistant.pending]);

  const newThread = async (): Promise<string> => {
    const created = await createThread();
    setThreadId(created.id);
    qc.setQueryData(["assistant", "thread", created.id], created);
    return created.id;
  };

  const send = async (question: string, task: Task, context: AskContext, fresh: boolean) => {
    if (abort.current) return; // one question at a time
    const id = fresh || !threadId ? await newThread() : threadId;
    const ctl = new AbortController();
    abort.current = ctl;
    setLive({ question, task, text: "", reasoning: "", meta: null, error: null });
    try {
      await ask(id!, { content: question, task, context: { page: window.location.pathname, ...context } }, (e) => {
        if (e.event === "meta") setLive((l) => l && { ...l, meta: e });
        else if (e.event === "token") setLive((l) => l && { ...l, text: l.text + e.text });
        else if (e.event === "reasoning") setLive((l) => l && { ...l, reasoning: l.reasoning + e.text });
        else if (e.event === "error") setLive((l) => l && { ...l, error: e });
      }, ctl.signal);
    } catch (err) {
      // refused before the stream (rate limit, bad request): nothing was stored, keep it shown
      const message = err instanceof ApiError ? err.problem.detail ?? err.message : String(err);
      setLive((l) => l && { ...l, error: { event: "error", message, hint: "", unavailable: false, docs: "", id: 0 } });
      abort.current = null;
      return;
    }
    abort.current = null;
    await qc.invalidateQueries({ queryKey: ["assistant"] });
    setLive(null); // the stored question and answer (or error) replace the live view
  };

  // a question from an entry point (job page, check, mode, scenario editor)
  useEffect(() => {
    if (!assistant.pending) return;
    const req = assistant.takePending();
    if (req) void send(req.question, req.task, req.context, req.newThread);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per request
  }, [assistant.pending]);

  useEffect(() => bottom.current?.scrollIntoView({ block: "end" }), [live?.text, thread.data?.messages?.length]);

  const submit = () => {
    const q = input.trim();
    if (!q) return;
    setInput("");
    void send(q, "chat", assistant.pageContext, false);
  };
  const busy = !!live && !live.error;
  const messages = thread.data?.messages ?? [];

  return (
    <aside className="assistant-drawer flex flex-col" aria-label={t("assistant.title")} data-testid="assistant-drawer">
      <div className="flex items-center gap-2 border-b border-default-200 p-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <i className="bi bi-stars text-secondary" /> {t("assistant.title")}
        </h2>
        <div className="flex-1" />
        <Tooltip content={t("assistant.model")}>
          <Button isIconOnly size="sm" variant={showModel ? "flat" : "light"} aria-label={t("assistant.model")} onPress={() => setShowModel((s) => !s)} data-testid="assistant-model-toggle">
            <i className="bi bi-cpu" />
          </Button>
        </Tooltip>
        <Tooltip content={t("assistant.newThread")}>
          <Button isIconOnly size="sm" variant="light" aria-label={t("assistant.newThread")} onPress={() => { setThreadId(null); setLive(null); }} isDisabled={busy} data-testid="assistant-new">
            <i className="bi bi-plus-lg" />
          </Button>
        </Tooltip>
        <Button isIconOnly size="sm" variant="light" aria-label={t("common.close")} onPress={onClose}>
          <i className="bi bi-x-lg" />
        </Button>
      </div>

      <div className="flex items-center gap-2 px-3 pt-2">
        <Select
          size="sm"
          aria-label={t("assistant.threads")}
          placeholder={t("assistant.newThread")}
          selectedKeys={threadId ? [threadId] : []}
          onSelectionChange={(keys) => { const k = [...keys][0]; if (k) { setThreadId(String(k)); setLive(null); } }}
          isDisabled={busy || !threads.data?.length}
          data-testid="assistant-threads"
        >
          {(threads.data ?? []).map((th) => (
            <SelectItem key={th.id}>{th.title || t("assistant.untitled")}</SelectItem>
          ))}
        </Select>
      </div>
      {showModel && (
        <ModelSwitch threadId={threadId} ensureThread={async () => threadId ?? (await newThread())} provider={thread.data?.provider ?? ""} model={thread.data?.model ?? ""} />
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3" data-testid="assistant-messages">
        {!messages.length && !live && <p className="text-sm opacity-70">{t("assistant.empty")}</p>}
        {messages.map((m) => (m.role === "user" ? <UserBubble key={m.id} text={m.content} task={m.task} /> : <Answer key={m.id} m={m} />))}
        {live && (
          <>
            <UserBubble text={live.question} task={live.task} />
            <LiveAnswer live={live} />
          </>
        )}
        <div ref={bottom} />
      </div>

      <div className="flex flex-col gap-2 border-t border-default-200 p-3">
        <Textarea
          minRows={2}
          maxRows={8}
          value={input}
          onValueChange={setInput}
          placeholder={t("assistant.placeholder")}
          aria-label={t("assistant.placeholder")}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          data-testid="assistant-input"
        />
        <div className="flex items-center gap-2">
          <span className="text-xs opacity-60">{t("assistant.advisory")}</span>
          <div className="flex-1" />
          {busy ? (
            <Button size="sm" color="danger" variant="flat" onPress={() => abort.current?.abort()} data-testid="assistant-stop">
              {t("assistant.stop")}
            </Button>
          ) : (
            <Button size="sm" color="secondary" onPress={submit} isDisabled={!input.trim()} data-testid="assistant-send">
              {t("assistant.send")}
            </Button>
          )}
        </div>
      </div>
    </aside>
  );
}

function UserBubble({ text, task }: { text: string; task: Task }) {
  const { t } = useI18n();
  return (
    <div className="ml-8 self-end rounded-lg bg-secondary-50 px-3 py-2 text-sm" data-testid="assistant-question">
      {task !== "chat" && <Chip size="sm" variant="flat" className="mb-1">{t(`assistant.task.${task}`)}</Chip>}
      <p className="whitespace-pre-wrap">{text}</p>
    </div>
  );
}

function LiveAnswer({ live }: { live: Live }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-2" data-testid="assistant-live">
      {!live.text && !live.error && (
        <div className="flex items-center gap-2 text-sm opacity-70">
          <Spinner size="sm" /> {live.reasoning ? t("assistant.thinking") : live.meta ? t("assistant.waiting", { model: live.meta.model }) : t("assistant.searching")}
        </div>
      )}
      {live.text && <Markdown text={live.text} />}
      {live.error && <ErrorBox message={live.error.message} hint={live.error.hint} unavailable={live.error.unavailable} />}
      {live.meta && <Footer provider={live.meta.provider} model={live.meta.model} sources={live.meta.sources.length} notes={live.meta.notes} />}
    </div>
  );
}

function Answer({ m }: { m: Message }) {
  const { t } = useI18n();
  const [highlight, setHighlight] = useState<number | null>(null);
  const [showReasoning, setShowReasoning] = useState(false);
  const byKey = new Map(m.proposals.map((p) => [`${p.kind}:${p.id}`, p]));
  return (
    <div className="flex flex-col gap-2" data-testid="assistant-answer">
      {m.content && (
        <Markdown
          text={m.content}
          onCite={(n) => setHighlight(n)}
          renderProposal={(kind, id) => {
            const p = byKey.get(`${kind}:${id}`);
            return p ? <ProposalButton p={p} /> : undefined;
          }}
        />
      )}
      {m.error && (
        <ErrorBox message={m.error === "stopped" ? t("assistant.stopped") : m.error} hint={m.extra.error?.hint ?? ""} unavailable={!!m.extra.error?.unavailable} />
      )}
      {m.extra.scenario && <ScenarioCard s={m.extra.scenario} />}
      {m.proposals.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="assistant-proposals">
          {m.proposals.map((p) => <ProposalButton key={`${p.kind}:${p.id}`} p={p} />)}
        </div>
      )}
      {m.sources.length > 0 && <Sources sources={m.sources} highlight={highlight} />}
      {m.reasoning && (
        <div>
          <button type="button" className="text-xs text-secondary hover:underline" onClick={() => setShowReasoning((s) => !s)} data-testid="assistant-reasoning-toggle">
            <i className={`bi ${showReasoning ? "bi-chevron-down" : "bi-chevron-right"}`} /> {t(showReasoning ? "assistant.hideReasoning" : "assistant.showReasoning")}
          </button>
          {showReasoning && <p className="mt-1 whitespace-pre-wrap rounded-md bg-default-100 p-2 text-xs opacity-80" data-testid="assistant-reasoning">{m.reasoning}</p>}
        </div>
      )}
      <Footer provider={m.provider} model={m.model} sources={m.sources.length} latency={m.latency_ms} notes={m.notes} />
    </div>
  );
}

function Sources({ sources, highlight }: { sources: Source[]; highlight: number | null }) {
  const { t } = useI18n();
  return (
    <details className="text-xs" open={highlight !== null} data-testid="assistant-sources">
      <summary className="cursor-pointer opacity-70">{t("assistant.sources", { n: sources.length })}</summary>
      <ol className="mt-1 flex flex-col gap-0.5">
        {sources.map((s) => (
          <li key={s.n} className={`rounded px-1 ${highlight === s.n ? "bg-secondary-100" : ""}`} data-testid={`source-${s.n}`}>
            <span className="font-semibold">[{s.n}]</span> <code className="cli">{s.source}</code> — {s.heading}
          </li>
        ))}
      </ol>
    </details>
  );
}

function Footer({ provider, model, sources, latency, notes }: { provider: string; model: string; sources: number; latency?: number | null; notes: string[] }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-0.5 text-[11px] opacity-60" data-testid="assistant-footer">
      <span>
        {provider} · {model} · {t("assistant.sourceCount", { n: sources })}
        {latency != null && ` · ${(latency / 1000).toFixed(1)} s`}
      </span>
      {notes.map((n) => <span key={n} className="text-warning-600">{n}</span>)}
    </div>
  );
}

function ErrorBox({ message, hint, unavailable }: { message: string; hint: string; unavailable: boolean }) {
  const { t } = useI18n();
  return (
    <div className="rounded-md border border-danger-200 bg-danger-50 p-2 text-sm" data-testid="assistant-error">
      <p className="text-danger">{message}</p>
      {hint && <p className="text-xs opacity-80">{hint}</p>}
      {unavailable && (
        <Link className="text-xs text-primary underline" href="/setup/">
          {t("assistant.aiChecks")}
        </Link>
      )}
    </div>
  );
}

function ProposalButton({ p }: { p: Proposal }) {
  const { t } = useI18n();
  const router = useRouter();
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  if (!p.step_id || p.params) {
    return (
      <span className="inline-flex items-center gap-1 align-middle">
        <code className="cli rounded bg-default-100 px-1 text-xs">{p.command}</code>
        <CopyButton text={p.command} />
      </span>
    );
  }
  if (confirm) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1 rounded-md border border-warning-300 px-2 py-1 align-middle text-xs" data-testid={`proposal-confirm-${p.id}`}>
        {t("assistant.confirmRun", { step: p.title })} <code className="cli">{p.command}</code>
        <Button size="sm" color="primary" onPress={async () => {
          try {
            const job = await startJob(p.step_id!);
            router.push(`/setup/job/?id=${job.id}`);
          } catch (e) {
            setError(e instanceof ApiError && e.status === 409 ? t("setup.busy") : String(e));
          }
        }} data-testid={`proposal-run-${p.id}`}>
          {t("assistant.run")}
        </Button>
        <Button size="sm" variant="light" onPress={() => setConfirm(false)}>{t("common.cancel")}</Button>
        {error && <span className="text-danger">{error}</span>}
      </span>
    );
  }
  return (
    <Button size="sm" variant="flat" color="primary" className="align-middle" startContent={<i className="bi bi-play" />} onPress={() => setConfirm(true)} data-testid={`proposal-${p.id}`}>
      {p.title}
    </Button>
  );
}

function ScenarioCard({ s }: { s: ScenarioSuggestion }) {
  const { t } = useI18n();
  const { scenarioTarget } = useAssistant();
  const [applied, setApplied] = useState(false);
  if (!s.yaml) return <p className="text-sm text-warning-600">{s.error}</p>;
  return (
    <div className="flex flex-col gap-1 rounded-md border border-default-200 p-2" data-testid="assistant-scenario">
      <div className="flex items-center gap-2 text-sm">
        <Chip size="sm" color={s.ok ? "success" : "danger"} variant="flat" data-testid="assistant-scenario-validity">
          {s.ok ? t("assistant.scenarioValid") : t("assistant.scenarioInvalid", { n: s.errors?.length ?? 0 })}
        </Chip>
        <div className="flex-1" />
        {scenarioTarget ? (
          <Button size="sm" color="primary" variant="flat" isDisabled={applied} onPress={() => { scenarioTarget(s.yaml!); setApplied(true); }} data-testid="assistant-scenario-apply">
            {applied ? t("assistant.applied") : t("assistant.applyToEditor")}
          </Button>
        ) : (
          <CopyButton text={s.yaml} />
        )}
      </div>
      {s.errors?.map((e, i) => <p key={i} className="text-xs text-danger">{e.line ? `${t("assistant.line", { n: e.line })}: ` : ""}{e.path} {e.message}</p>)}
      <pre className="cli max-h-64 overflow-auto rounded bg-neutral-950 p-2 text-[11px] leading-4 text-neutral-100" data-testid="assistant-scenario-diff">
        {(s.diff || t("assistant.noChanges")).split("\n").map((line, i) => (
          <div key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "text-green-400" : line.startsWith("-") && !line.startsWith("---") ? "text-red-400" : ""}>
            {line || " "}
          </div>
        ))}
      </pre>
    </div>
  );
}

/** Per-thread provider and model (§14.3); empty = the task settings of Settings → AI. */
function ModelSwitch({
  threadId,
  ensureThread,
  provider,
  model,
}: {
  threadId: string | null;
  ensureThread: () => Promise<string>;
  provider: string;
  model: string;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["ai", "models"], queryFn: getAiModels, staleTime: 60_000 });
  const [p, setP] = useState(provider);
  const [m, setM] = useState(model);
  useEffect(() => { setP(provider); setM(model); }, [provider, model, threadId]);
  const installed = (models.data?.ollama.models ?? []).filter((x) => !x.capabilities.includes("embedding") || x.capabilities.includes("completion"));
  const save = async (np: string, nm: string) => {
    setP(np);
    setM(nm);
    const id = await ensureThread();
    await updateThread(id, { provider: np, model: nm });
    await qc.invalidateQueries({ queryKey: ["assistant", "thread", id] });
  };
  return (
    <div className="flex flex-col gap-2 border-b border-default-200 p-3 text-sm" data-testid="assistant-model">
      <div className="flex gap-2">
        <Select size="sm" label={t("ai.provider")} selectedKeys={[p || "default"]} onSelectionChange={(k) => { const v = String([...k][0] ?? "default"); void save(v === "default" ? "" : v, ""); }} data-testid="assistant-provider">
          <SelectItem key="default">{t("assistant.taskDefault")}</SelectItem>
          <SelectItem key="ollama">Ollama</SelectItem>
          <SelectItem key="anthropic">Anthropic</SelectItem>
        </Select>
        {p === "ollama" && (
          <Select size="sm" label={t("ai.model")} selectedKeys={m ? [m] : []} onSelectionChange={(k) => void save(p, String([...k][0] ?? ""))} data-testid="assistant-model-select">
            {installed.map((x) => <SelectItem key={x.name}>{x.name}</SelectItem>)}
          </Select>
        )}
      </div>
      {p === "anthropic" && !models.data?.anthropic.allowed && <p className="text-xs text-warning-600">{t("ai.anthropicOff")}</p>}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button, Card, CardBody, CardHeader, Chip, Input, Select, SelectItem, Spinner } from "@heroui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AI_TASKS,
  getAiModels,
  getIndexStatus,
  rebuildIndex,
  saveTask,
  testTask,
  type AiModels,
  type AiTask,
  type TaskConfig,
  type TestResult,
} from "@/lib/api/assistant";
import { startJob } from "@/lib/api/cockpit";
import { ApiError } from "@/lib/api/http";
import { useI18n } from "@/lib/i18n";

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(1)} GB`;

/**
 * Settings → AI (docs/specs/spec_cockpit.md §14.3): the installed Ollama models with a hardware
 * hint, pulling a model, provider + model + parameters per task with a test call, and the
 * knowledge index. A model chosen here applies to the next question, without a restart.
 */
export function AiTab() {
  const { t } = useI18n();
  const models = useQuery({ queryKey: ["ai", "models"], queryFn: getAiModels });
  if (models.isLoading) return <Spinner label={t("common.loading")} />;
  if (models.error || !models.data) return <p className="text-danger">{t("common.error", { detail: String(models.error) })}</p>;
  const data = models.data;
  return (
    <div className="flex flex-col gap-4" data-testid="ai-tab">
      {data.fake && <p className="text-sm text-warning-600">{t("ai.fake")}</p>}
      <div className="grid gap-4 xl:grid-cols-2">
        {AI_TASKS.map((task) => <TaskCard key={task} task={task} cfg={data.tasks[task]} data={data} />)}
      </div>
      <ModelsCard data={data} />
      <AnthropicCard data={data} />
      <IndexCard />
    </div>
  );
}

function SourceChip({ source }: { source: string }) {
  const color = source === "settings" ? "secondary" : source === "env" || source === ".env" ? "warning" : "default";
  return <Chip size="sm" variant="flat" color={color} className="ml-1 align-middle text-[10px]">{source}</Chip>;
}

function TaskCard({ task, cfg, data }: { task: AiTask; cfg: TaskConfig; data: AiModels }) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const [form, setForm] = useState(cfg);
  const [result, setResult] = useState<TestResult | null>(null);
  useEffect(() => setForm(cfg), [cfg]);
  const installed = data.ollama.models.filter((m) => (task === "embed" ? m.capabilities.includes("embedding") : !m.capabilities.includes("embedding") || m.capabilities.includes("completion")));
  const save = useMutation({
    mutationFn: () =>
      saveTask(task, {
        ...(task === "embed" ? {} : { provider: form.provider }),
        model: form.model,
        temperature: form.temperature,
        max_tokens: form.max_tokens,
        num_ctx: form.num_ctx,
        timeout_s: form.timeout_s,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["ai"] });
      if (form.provider === "anthropic") test.mutate(); // an Anthropic model id is checked by a call (§14.3)
    },
  });
  const test = useMutation({ mutationFn: () => testTask(task), onSuccess: setResult, onMutate: () => setResult(null) });
  const overridden = ["env", ".env"].includes(cfg.sources.model);
  const hint = data.ollama.models.find((m) => m.name === form.model)?.hint;
  const num = (key: "temperature" | "max_tokens" | "num_ctx" | "timeout_s", label: string, step = "1") => (
    <Input
      size="sm"
      type="number"
      step={step}
      label={<>{label}<SourceChip source={cfg.sources[key]} /></>}
      value={String(form[key])}
      onValueChange={(v) => setForm({ ...form, [key]: Number(v) })}
      data-testid={`ai-${task}-${key}`}
    />
  );
  return (
    <Card data-testid={`ai-task-${task}`}>
      <CardHeader className="flex flex-col items-start gap-0.5">
        <h3 className="font-semibold">{t(`ai.task.${task}`)}</h3>
        <p className="text-xs opacity-70">{t(`ai.taskHelp.${task}`)}</p>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-2">
          <Select
            size="sm"
            label={<>{t("ai.provider")}<SourceChip source={cfg.sources.provider} /></>}
            selectedKeys={[form.provider]}
            isDisabled={task === "embed"}
            onSelectionChange={(k) => {
              const provider = String([...k][0] ?? "ollama") as TaskConfig["provider"];
              setForm({ ...form, provider, model: provider === "anthropic" ? data.anthropic.default_model : installed[0]?.name ?? "" });
            }}
            data-testid={`ai-${task}-provider`}
          >
            <SelectItem key="ollama">Ollama</SelectItem>
            <SelectItem key="anthropic">Anthropic</SelectItem>
          </Select>
          {form.provider === "ollama" ? (
            <Select
              size="sm"
              label={<>{t("ai.model")}<SourceChip source={cfg.sources.model} /></>}
              selectedKeys={installed.some((m) => m.name === form.model) ? [form.model] : []}
              placeholder={form.model}
              onSelectionChange={(k) => setForm({ ...form, model: String([...k][0] ?? form.model) })}
              data-testid={`ai-${task}-model`}
            >
              {installed.map((m) => <SelectItem key={m.name}>{m.name}</SelectItem>)}
            </Select>
          ) : (
            <Input size="sm" label={<>{t("ai.anthropicModel")}<SourceChip source={cfg.sources.model} /></>} value={form.model} onValueChange={(v) => setForm({ ...form, model: v })} data-testid={`ai-${task}-model-id`} />
          )}
        </div>
        {task !== "embed" && (
          <div className="grid grid-cols-4 gap-2">
            {num("temperature", t("ai.temperature"), "0.1")}
            {num("max_tokens", t("ai.maxTokens"))}
            {num("num_ctx", t("ai.numCtx"))}
            {num("timeout_s", t("ai.timeout"))}
          </div>
        )}
        {overridden && <p className="text-xs text-warning-600">{t("ai.overridden", { source: cfg.sources.model })}</p>}
        {form.provider === "ollama" && !installed.some((m) => m.name === form.model) && <p className="text-xs text-warning-600">{t("ai.notInstalled", { model: form.model })}</p>}
        {form.provider === "anthropic" && !data.anthropic.allowed && <p className="text-xs text-warning-600">{t("ai.anthropicOff")}</p>}
        {hint && form.provider === "ollama" && <p className={`text-xs ${hint.fits ? "opacity-70" : "text-warning-600"}`}>{hint.text}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" color="primary" isLoading={save.isPending} onPress={() => save.mutate()} data-testid={`ai-${task}-save`}>
            {t("common.save")}
          </Button>
          <Button size="sm" variant="flat" isLoading={test.isPending} onPress={() => test.mutate()} data-testid={`ai-${task}-test`}>
            {t("ai.test")}
          </Button>
          {save.error && <span className="text-xs text-danger">{save.error instanceof ApiError ? save.error.problem.detail : String(save.error)}</span>}
        </div>
        {result && (
          <div className={`rounded-md p-2 text-xs ${result.ok ? "bg-success-50" : "bg-danger-50"}`} data-testid={`ai-${task}-result`}>
            {result.ok ? (
              <>
                <p>
                  {result.provider} · {result.model} · {t("ai.latency", { ms: result.latency_ms ?? 0 })}
                  {result.first_token_ms != null && ` · ${t("ai.firstToken", { ms: result.first_token_ms })}`}
                </p>
                <p className="italic opacity-80">{result.text}</p>
                {result.notes?.map((n) => <p key={n} className="text-warning-600">{n}</p>)}
              </>
            ) : (
              <>
                <p className="text-danger">{result.error}</p>
                {result.hint && <p>{result.hint}</p>}
              </>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function ModelsCard({ data }: { data: AiModels }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const pull = useMutation({ mutationFn: () => startJob("ollama.pull", { model: name.trim() }) });
  return (
    <Card data-testid="ai-models">
      <CardHeader className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{t("ai.models")}</h3>
        <Chip size="sm" variant="flat" color={data.ollama.up ? "success" : "danger"}>
          {data.ollama.up ? t("ai.ollamaUp", { url: data.ollama.base_url }) : t("ai.ollamaDown", { url: data.ollama.base_url })}
        </Chip>
        <span className="text-xs opacity-70">
          {t("ai.memory", { ram: data.memory.ram_gb })}
          {data.memory.gpus.map((g) => ` · ${g.name} ${g.mem_gb} GB`).join("")}
        </span>
      </CardHeader>
      <CardBody className="flex flex-col gap-3">
        {!data.ollama.up && (
          <p className="text-sm text-danger">
            {data.ollama.error}{" "}
            <Link className="text-primary underline" href="/setup/">{t("assistant.aiChecks")}</Link>
          </p>
        )}
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs opacity-60">
              <th className="py-1">{t("ai.model")}</th>
              <th>{t("ai.size")}</th>
              <th>{t("ai.quantization")}</th>
              <th>{t("ai.capabilities")}</th>
              <th>{t("ai.hint")}</th>
            </tr>
          </thead>
          <tbody>
            {data.ollama.models.map((m) => (
              <tr key={m.name} className="border-t border-default-100" data-testid={`ai-model-${m.name}`}>
                <td className="py-1 font-mono text-xs">{m.name}</td>
                <td>{gb(m.size)}</td>
                <td className="text-xs">{m.quantization}</td>
                <td className="text-xs">{m.capabilities.join(", ")}</td>
                <td className={`text-xs ${m.hint.fits ? "opacity-70" : "text-warning-600"}`}>{m.hint.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex flex-wrap items-center gap-2">
          <Input size="sm" className="max-w-xs" label={t("ai.pullLabel")} placeholder="gemma3:4b" value={name} onValueChange={setName} data-testid="ai-pull-name" />
          <Button size="sm" variant="flat" color="primary" isDisabled={!name.trim()} isLoading={pull.isPending} onPress={() => pull.mutate()} data-testid="ai-pull">
            {t("ai.pull")}
          </Button>
          {pull.data && <Link className="text-sm text-primary underline" href={`/setup/job/?id=${pull.data.id}`}>{t("ai.pullJob")}</Link>}
          {pull.error && <span className="text-xs text-danger">{pull.error instanceof ApiError ? pull.error.problem.detail : String(pull.error)}</span>}
        </div>
      </CardBody>
    </Card>
  );
}

function AnthropicCard({ data }: { data: AiModels }) {
  const { t } = useI18n();
  const a = data.anthropic;
  return (
    <Card data-testid="ai-anthropic">
      <CardHeader className="flex items-center gap-2">
        <h3 className="font-semibold">Anthropic API</h3>
        <Chip size="sm" variant="flat" color={a.allowed ? "success" : "default"}>{a.allowed ? t("ai.allowed") : t("ai.notAllowed")}</Chip>
      </CardHeader>
      <CardBody className="flex flex-col gap-1 text-sm">
        <p>ASSISTANT_ALLOW_EXTERNAL={a.flag} · ANTHROPIC_API_KEY {a.key} · ANTHROPIC_MODEL {a.default_model}</p>
        <p className="text-xs opacity-70">{t("ai.anthropicHelp")}</p>
      </CardBody>
    </Card>
  );
}

function IndexCard() {
  const { t } = useI18n();
  const status = useQuery({ queryKey: ["ai", "index"], queryFn: getIndexStatus, refetchInterval: 10_000 });
  const rebuild = useMutation({ mutationFn: () => rebuildIndex(true) });
  const s = status.data;
  return (
    <Card data-testid="ai-index">
      <CardHeader className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{t("ai.index")}</h3>
        {s && (
          <Chip size="sm" variant="flat" color={s.chunks === 0 ? "danger" : s.degraded ? "warning" : "success"} data-testid="ai-index-state">
            {s.chunks === 0 ? t("ai.indexEmpty") : s.degraded ? t("ai.indexDegraded") : t("ai.indexHybrid")}
          </Chip>
        )}
      </CardHeader>
      <CardBody className="flex flex-col gap-2 text-sm">
        {s && (
          <>
            <p data-testid="ai-index-summary">
              {t("ai.indexSummary", { files: s.files ?? 0, chunks: s.chunks, embedded: s.embedded })}
              {s.embed_model && ` · ${s.embed_model}${s.dim ? ` (${s.dim})` : ""}`}
              {s.built && ` · ${new Date(s.built).toLocaleString()}`}
            </p>
            <p className="text-xs opacity-70">{Object.entries(s.by_kind).map(([k, n]) => `${k}: ${n}`).join(" · ")} · {t("ai.excluded", { paths: s.excluded.join(", ") })}</p>
            {s.error && <p className="text-xs text-warning-600">{s.error}</p>}
          </>
        )}
        <div className="flex items-center gap-2">
          <Button size="sm" variant="flat" color="primary" isLoading={rebuild.isPending} onPress={() => rebuild.mutate()} data-testid="ai-index-rebuild">
            <i className="bi bi-arrow-repeat" /> {t("ai.rebuild")}
          </Button>
          {rebuild.data && <Link className="text-sm text-primary underline" href={`/setup/job/?id=${rebuild.data.id}`} data-testid="ai-index-job">{t("ai.rebuildJob")}</Link>}
          {rebuild.error && <span className="text-xs text-danger">{String(rebuild.error)}</span>}
        </div>
      </CardBody>
    </Card>
  );
}

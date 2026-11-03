"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Card,
  CardBody,
  Checkbox,
  Chip,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Radio,
  RadioGroup,
  Select,
  SelectItem,
  Spinner,
} from "@heroui/react";
import {
  deleteScenario,
  downloadPack,
  duplicateScenario,
  exportUrl,
  importScenario,
  listScenarios,
  unpackApply,
  unpackPreview,
  type UnpackPreview,
} from "@/lib/api/scenarios";
import { ApiError } from "@/lib/api/http";
import { useI18n } from "@/lib/i18n";
import { CliLine } from "@/components/common/CopyButton";
import { RESULT_COLOR } from "@/components/scenarios/colors";

/** Simulations → scenario list (docs/specs/spec_cockpit.md §12.3). */
export default function SimulationsPage() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["scenarios"], queryFn: listScenarios });
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [preview, setPreview] = useState<UnpackPreview | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const yamlInput = useRef<HTMLInputElement>(null);
  const packInput = useRef<HTMLInputElement>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["scenarios"] });
  const fail = (e: Error) => setMsg({ text: e instanceof ApiError ? e.message : String(e), error: true });

  const del = useMutation({ mutationFn: deleteScenario, onSuccess: (r) => (setMsg({ text: t("sim.deleted", { id: r.deleted }) }), refresh()), onError: fail });
  const dup = useMutation({ mutationFn: duplicateScenario, onSuccess: (r) => (setMsg({ text: t("sim.duplicated", { id: r.id }) }), refresh()), onError: fail });
  const apply = useMutation({
    mutationFn: () => unpackApply(preview!.token, decisions),
    onSuccess: (r) => {
      setMsg({ text: r.done.map((d) => `${d.id}: ${d.action}`).join(", ") });
      setPreview(null);
      refresh();
    },
    onError: fail,
  });

  const tags = useMemo(() => Array.from(new Set((list.data ?? []).flatMap((s) => s.tags))).sort(), [list.data]);
  const rows = (list.data ?? []).filter(
    (s) => (!tag || s.tags.includes(tag)) && (!q || `${s.id} ${s.name} ${s.description}`.toLowerCase().includes(q.toLowerCase())),
  );

  const onYaml = async (file: File | undefined) => {
    if (!file) return;
    const text = await file.text();
    try {
      const r = await importScenario(text).catch(async (e) => {
        if (e instanceof ApiError && e.status === 409 && window.confirm(t("sim.replaceExisting"))) return importScenario(text, true);
        throw e;
      });
      setMsg({ text: t("sim.imported", { id: r.id, v: r.version }) });
      refresh();
    } catch (e) {
      fail(e as Error);
    }
  };
  const onPack = async (file: File | undefined) => {
    if (!file) return;
    try {
      const p = await unpackPreview(file);
      setPreview(p);
      setDecisions(Object.fromEntries(p.scenarios.map((s) => [s.id, s.default])));
    } catch (e) {
      fail(e as Error);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{t("nav.simulations")}</h1>
        <Button as={Link} href="/simulations/edit/" color="primary" size="sm" data-testid="new-scenario">
          <i className="bi bi-plus-lg" /> {t("sim.new")}
        </Button>
        <Button size="sm" variant="flat" onPress={() => yamlInput.current?.click()} data-testid="import-yaml">
          <i className="bi bi-upload" /> {t("sim.importYaml")}
        </Button>
        <Button size="sm" variant="flat" isDisabled={!picked.size} onPress={() => downloadPack([...picked]).catch(fail)} data-testid="pack">
          <i className="bi bi-file-zip" /> {t("sim.pack", { n: picked.size })}
        </Button>
        <Button size="sm" variant="flat" onPress={() => packInput.current?.click()} data-testid="unpack">
          <i className="bi bi-box-arrow-in-down" /> {t("sim.unpack")}
        </Button>
        <input ref={yamlInput} type="file" accept=".yaml,.yml" hidden onChange={(e) => onYaml(e.target.files?.[0])} data-testid="import-yaml-file" />
        <input ref={packInput} type="file" accept=".zip" hidden onChange={(e) => onPack(e.target.files?.[0])} data-testid="unpack-file" />
      </div>
      <p className="text-sm opacity-80">{t("sim.intro")}</p>
      {msg && (
        <p className={`text-sm ${msg.error ? "text-danger" : ""}`} role="status" data-testid="sim-msg">
          {msg.text}
        </p>
      )}
      <Card>
        <CardBody className="flex flex-row flex-wrap items-end gap-3">
          <Input size="sm" className="w-72" label={t("sim.search")} value={q} onValueChange={setQ} data-testid="sim-search" />
          <Select
            size="sm"
            className="w-48"
            label={t("sim.tag")}
            selectedKeys={[tag || "__all"]}
            onSelectionChange={(k) => {
              const v = Array.from(k)[0] as string;
              setTag(v === "__all" ? "" : v);
            }}
          >
            {[<SelectItem key="__all">{t("sim.allTags")}</SelectItem>, ...tags.map((x) => <SelectItem key={x}>{x}</SelectItem>)]}
          </Select>
        </CardBody>
      </Card>
      {list.isLoading ? (
        <Spinner />
      ) : (
        <table className="w-full text-sm" data-testid="scenario-table">
          <thead className="text-left opacity-70">
            <tr>
              <th />
              <th className="py-1 pr-3 font-medium">{t("sim.name")}</th>
              <th className="py-1 pr-3 font-medium">{t("sim.tags")}</th>
              <th className="py-1 pr-3 font-medium">{t("sim.modes")}</th>
              <th className="py-1 pr-3 font-medium">{t("sim.task")}</th>
              <th className="py-1 pr-3 font-medium">{t("sim.lastRun")}</th>
              <th className="py-1 pr-3 font-medium">v</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className="border-t border-default-200" data-testid={`scenario-${s.id}`}>
                <td className="py-1 pr-2">
                  <Checkbox
                    size="sm"
                    aria-label={s.id}
                    isSelected={picked.has(s.id)}
                    onValueChange={(on) => setPicked((old) => {
                      const n = new Set(old);
                      if (on) n.add(s.id);
                      else n.delete(s.id);
                      return n;
                    })}
                  />
                </td>
                <td className="py-1 pr-3">
                  <Link href={`/simulations/edit/?id=${s.id}`} className="font-medium text-primary underline">
                    {s.name}
                  </Link>
                  <div className="font-mono text-xs opacity-60">{s.id}</div>
                </td>
                <td className="py-1 pr-3">
                  {s.tags.map((x) => (
                    <Chip key={x} size="sm" variant="flat" className="mr-1">
                      {x}
                    </Chip>
                  ))}
                </td>
                <td className="py-1 pr-3 text-xs">{s.modes.join(", ")}</td>
                <td className="py-1 pr-3 text-xs">
                  {s.task_type} {s.task_type === "sequence" ? `(${s.steps})` : ""}
                </td>
                <td className="py-1 pr-3 text-xs">
                  {s.last_run ? (
                    <Link href={`/simulations/run/?id=${s.id}&run=${s.last_run.id}`}>
                      <Chip size="sm" variant="flat" color={RESULT_COLOR[s.last_run.result] ?? "default"}>
                        {s.last_run.result}
                      </Chip>{" "}
                      {new Date(s.last_run.started).toLocaleString()}
                    </Link>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="py-1 pr-3">{s.version}</td>
                <td className="whitespace-nowrap py-1 text-right">
                  <Button as={Link} href={`/simulations/run/?id=${s.id}`} size="sm" color="primary" variant="flat" isDisabled={s.task_type !== "sequence"} data-testid={`run-${s.id}`}>
                    <i className="bi bi-play-fill" /> {t("sim.run")}
                  </Button>
                  <Button size="sm" variant="light" isIconOnly aria-label={t("sim.duplicate")} onPress={() => dup.mutate(s.id)} data-testid={`dup-${s.id}`}>
                    <i className="bi bi-copy" />
                  </Button>
                  <Button as="a" href={exportUrl(s.id)} size="sm" variant="light" isIconOnly aria-label={t("sim.export")}>
                    <i className="bi bi-download" />
                  </Button>
                  <Button size="sm" variant="light" color="danger" isIconOnly aria-label={t("common.delete")} onPress={() => setConfirm(s.id)} data-testid={`del-${s.id}`}>
                    <i className="bi bi-trash" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="flex flex-col gap-1 text-xs opacity-80">
        <span>{t("sim.cli")}</span>
        <CliLine command="make scenario-run ID=<id>" />
        <CliLine command='make scenario-pack IDS="<id> <id>" OUT=pack.zip' />
      </div>

      <Modal isOpen={!!confirm} onOpenChange={() => setConfirm(null)}>
        <ModalContent>
          <ModalHeader>{t("sim.deleteTitle")}</ModalHeader>
          <ModalBody>{t("sim.deleteBody", { id: confirm ?? "" })}</ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={() => setConfirm(null)}>
              {t("common.cancel")}
            </Button>
            <Button color="danger" onPress={() => (del.mutate(confirm!), setConfirm(null))} data-testid="confirm-delete">
              {t("common.delete")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <Modal size="3xl" isOpen={!!preview} onOpenChange={() => setPreview(null)} scrollBehavior="inside">
        <ModalContent>
          <ModalHeader>{t("sim.unpackTitle", { host: preview?.source_host ?? "" })}</ModalHeader>
          <ModalBody>
            <p className="text-xs opacity-70">
              {preview?.created} · commit {preview?.git_commit.slice(0, 10)}
            </p>
            {preview?.config_warnings.map((w) => (
              <p key={w} className="text-sm text-warning">
                <i className="bi bi-exclamation-triangle" /> {w}
              </p>
            ))}
            {preview?.scenarios.map((s) => (
              <Card key={s.id} shadow="sm" data-testid={`unpack-${s.id}`}>
                <CardBody className="gap-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono">{s.id}</span>
                    <span>v{s.version}</span>
                    <Chip size="sm" variant="flat" color={s.status === "new" ? "success" : s.status === "same" ? "default" : "warning"}>
                      {t(`sim.status.${s.status}` as "sim.status.new")}
                      {s.existing_version != null ? ` (v${s.existing_version} here)` : ""}
                    </Chip>
                    {s.errors.length > 0 && <Chip size="sm" color="danger">{t("sim.invalid")}</Chip>}
                  </div>
                  <RadioGroup
                    orientation="horizontal"
                    size="sm"
                    value={decisions[s.id]}
                    onValueChange={(v) => setDecisions((d) => ({ ...d, [s.id]: v }))}
                  >
                    <Radio value="keep">{t("sim.keep")}</Radio>
                    <Radio value="replace">{s.status === "new" ? t("sim.add") : t("sim.replace")}</Radio>
                    {s.status !== "new" && <Radio value="copy">{t("sim.copy")}</Radio>}
                  </RadioGroup>
                  {s.diff && <pre className="max-h-40 overflow-auto rounded bg-default-100 p-2 text-xs">{s.diff}</pre>}
                </CardBody>
              </Card>
            ))}
          </ModalBody>
          <ModalFooter>
            <Button variant="light" onPress={() => setPreview(null)}>
              {t("common.cancel")}
            </Button>
            <Button color="primary" isLoading={apply.isPending} onPress={() => apply.mutate()} data-testid="unpack-apply">
              {t("sim.importNow")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}

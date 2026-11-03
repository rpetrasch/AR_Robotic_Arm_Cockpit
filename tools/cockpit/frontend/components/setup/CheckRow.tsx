"use client";

import { useState } from "react";
import { Button, Tooltip } from "@heroui/react";
import type { Check, Step } from "@/lib/api/cockpit";
import { useI18n } from "@/lib/i18n";
import { useAssistant } from "@/lib/assistant";
import { CliLine } from "@/components/common/CopyButton";
import { StatusIcon } from "./StatusIcon";

/**
 * One checklist item (docs/specs/spec_cockpit.md §8.1): status, what was found, and the fix: a
 * step the cockpit runs (button), or instructions to copy (needs root / manual). "Run it
 * yourself" shows the commands behind both (principle 8).
 */
export function CheckRow({
  check,
  step,
  blockedBy,
  rechecking,
  starting,
  onRecheck,
  onRun,
}: {
  check: Check;
  step?: Step;
  blockedBy: string[];
  rechecking: boolean;
  starting: boolean;
  onRecheck: () => void;
  onRun: (step: Step, params: Record<string, string>) => void;
}) {
  const { t } = useI18n();
  const assistant = useAssistant();
  const failing = check.status === "fail" || check.status === "warn";
  const runnable = failing && step && step.kind !== "manual" && step.available;
  const manual = failing && (!step || step.kind === "manual") && (check.fix || check.fix_commands.length || step);
  const [open, setOpen] = useState<"" | "fix" | "cli">("");
  const toggle = (which: "fix" | "cli") => setOpen((o) => (o === which ? "" : which));
  const stepCommand = step && step.kind !== "manual" ? commandWith(step, check.fix_params) : null;

  return (
    <div className="flex flex-col gap-2 py-2.5" data-testid={`check-${check.id}`} data-status={check.status}>
      <div className="flex items-start gap-3">
        <StatusIcon status={rechecking ? "running" : check.status} className="mt-0.5 text-lg" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{check.title}</div>
          <div className="text-sm opacity-75" data-testid={`detail-${check.id}`}>
            {check.detail}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {runnable && (
            <Tooltip content={blockedBy.length ? t("setup.requires", { items: blockedBy.join(", ") }) : step.description}>
              <span>
                <Button
                  size="sm"
                  color="primary"
                  variant="flat"
                  isDisabled={blockedBy.length > 0}
                  isLoading={starting}
                  startContent={<i className="bi bi-play-fill" />}
                  onPress={() => onRun(step, check.fix_params)}
                  data-testid={`fix-${check.id}`}
                >
                  {step.title}
                </Button>
              </span>
            </Tooltip>
          )}
          {manual && (
            <Button size="sm" variant="light" onPress={() => toggle("fix")} data-testid={`howto-${check.id}`}>
              {t("setup.howToFix")}
            </Button>
          )}
          <Button size="sm" variant="light" onPress={() => toggle("cli")} data-testid={`cli-${check.id}`}>
            {t("setup.runYourself")}
          </Button>
          <Tooltip content={t("setup.explainCheck")}>
            <Button
              isIconOnly
              size="sm"
              variant="light"
              aria-label={t("setup.explainCheck")}
              onPress={() => assistant.ask({ question: t("setup.explainQuestion", { title: check.title, status: check.status }), task: "chat", context: { check_id: check.id } })}
              data-testid={`explain-${check.id}`}
            >
              <i className="bi bi-stars text-secondary" />
            </Button>
          </Tooltip>
          <Tooltip content={t("setup.recheck")}>
            <Button isIconOnly size="sm" variant="light" aria-label={t("setup.recheck")} onPress={onRecheck} isDisabled={rechecking}>
              <i className="bi bi-arrow-clockwise" />
            </Button>
          </Tooltip>
        </div>
      </div>
      {open === "fix" && (
        <div className="ml-8 flex flex-col gap-1.5 text-sm" data-testid={`fix-text-${check.id}`}>
          {check.fix && <p>{check.fix}</p>}
          {step?.kind === "manual" && <p className="opacity-80">{t("setup.needsRoot")}</p>}
          {(check.fix_commands.length ? check.fix_commands : (step?.instructions ?? []))
            .filter((c) => c !== step?.doc_url) // shown as the link below instead
            .map((c) => (
            <CliLine key={c} command={c} />
          ))}
          {step?.instructions_text && <p className="opacity-80">{step.instructions_text}</p>}
          {step?.doc_url && (
            <a
              href={step.doc_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-primary hover:underline"
              data-testid={`guide-${check.id}`}
            >
              <i className="bi bi-box-arrow-up-right" />
              {t("setup.officialGuide")}
              <span className="opacity-60">({new URL(step.doc_url).host})</span>
            </a>
          )}
        </div>
      )}
      {open === "cli" && (
        <div className="ml-8 flex flex-col gap-1.5 text-sm" data-testid={`cli-text-${check.id}`}>
          {check.cli.map((c) => (
            <CliLine key={c} command={c} />
          ))}
          {stepCommand && !check.cli.includes(stepCommand) && <CliLine command={stepCommand} />}
          <p className="text-xs opacity-60">
            {t("setup.lastChecked", { when: new Date(check.last_checked).toLocaleTimeString() })} ·{" "}
            {t("setup.docs", { path: check.docs })}
          </p>
        </div>
      )}
    </div>
  );
}

function commandWith(step: Step, params: Record<string, string>): string {
  return step.command.replace(/\{(\w+)\}/g, (m, name: string) => params?.[name] ?? m);
}

"use client";

import { useState } from "react";
import { Button, Tooltip } from "@heroui/react";
import { useI18n } from "@/lib/i18n";

/** Copies `text` to the clipboard (principle 8: every command is copyable). */
export function CopyButton({ text, label }: { text: string; label?: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // http:// on a LAN address has no clipboard API: select-and-copy still works
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Tooltip content={copied ? t("common.copied") : (label ?? t("common.copy"))}>
      <Button isIconOnly size="sm" variant="light" aria-label={label ?? t("common.copy")} onPress={copy}>
        <i className={`bi ${copied ? "bi-check2" : "bi-clipboard"}`} />
      </Button>
    </Tooltip>
  );
}

/** A command line with a copy button ("Run it yourself"). */
export function CliLine({ command }: { command: string }) {
  return (
    <div className="flex items-center gap-1 rounded-md bg-default-100 py-0.5 pl-3 pr-1">
      <code className="cli flex-1 overflow-x-auto whitespace-pre">{command}</code>
      <CopyButton text={command} />
    </div>
  );
}

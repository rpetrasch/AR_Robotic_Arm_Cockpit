"use client";

import { Fragment, type ReactNode } from "react";

/**
 * The Markdown subset the assistant writes: headings, paragraphs, lists, code blocks, inline
 * code, bold, links, citations [1] and proposed steps [[step:id]]. Built as React elements, never
 * as HTML, so an answer cannot inject markup.
 */
export function Markdown({
  text,
  onCite,
  renderProposal,
}: {
  text: string;
  onCite?: (n: number) => void;
  renderProposal?: (kind: "step" | "make", id: string) => ReactNode;
}) {
  const blocks: ReactNode[] = [];
  const parts = text.split(/^```[ \t]*([\w-]*)[ \t]*\n/m);
  // parts: [text, lang, code+rest, lang, code+rest, ...]
  let key = 0;
  blocks.push(...prose(parts[0], () => key++, onCite, renderProposal));
  for (let i = 1; i < parts.length; i += 2) {
    const lang = parts[i];
    const rest = parts[i + 1] ?? "";
    const close = rest.search(/^```[ \t]*$/m);
    const code = close >= 0 ? rest.slice(0, close) : rest; // still streaming: no closing fence yet
    blocks.push(
      <pre key={key++} className="cli my-2 overflow-x-auto rounded-md bg-neutral-950 p-2 text-xs text-neutral-100" data-lang={lang || undefined}>
        <code>{code.replace(/\n$/, "")}</code>
      </pre>,
    );
    if (close >= 0) blocks.push(...prose(rest.slice(close).replace(/^```[ \t]*\n?/, ""), () => key++, onCite, renderProposal));
  }
  return <div className="flex flex-col gap-1 text-sm leading-6">{blocks}</div>;
}

function prose(
  text: string,
  key: () => number,
  onCite?: (n: number) => void,
  renderProposal?: (kind: "step" | "make", id: string) => ReactNode,
): ReactNode[] {
  const out: ReactNode[] = [];
  const lines = text.split("\n");
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const inl = (s: string) => inline(s, onCite, renderProposal);
  const flushPara = () => {
    if (para.length) out.push(<p key={key()}>{inl(para.join(" "))}</p>);
    para = [];
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((it, i) => <li key={i}>{inl(it)}</li>);
      out.push(list.ordered ? <ol key={key()} className="ml-5 list-decimal">{items}</ol> : <ul key={key()} className="ml-5 list-disc">{items}</ul>);
    }
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (heading) {
      flushPara();
      flushList();
      const size = heading[1].length <= 2 ? "text-base" : "text-sm";
      out.push(<h4 key={key()} className={`mt-2 font-semibold ${size}`}>{inl(heading[2])}</h4>);
    } else if (bullet || numbered) {
      flushPara();
      const ordered = !bullet;
      if (list && list.ordered !== ordered) flushList();
      list = list ?? { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]);
    } else if (!line.trim()) {
      flushPara();
      flushList();
    } else if (list && /^\s{2,}/.test(raw)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`; // continuation of an item
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return out;
}

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\[(?:step|make):[\w.:-]+\]\]|\[\d+(?:\s*[,–-]\s*\d+)*\]|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;

function inline(
  text: string,
  onCite?: (n: number) => void,
  renderProposal?: (kind: "step" | "make", id: string) => ReactNode,
): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (!part) return null;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      return <code key={i} className="cli rounded bg-default-100 px-1 text-xs">{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    const proposal = part.match(/^\[\[(step|make):([\w.:-]+)\]\]$/);
    if (proposal) {
      const rendered = renderProposal?.(proposal[1] as "step" | "make", proposal[2]);
      return <Fragment key={i}>{rendered ?? <code className="cli text-xs">{proposal[1] === "make" ? `make ${proposal[2]}` : proposal[2]}</code>}</Fragment>;
    }
    const cite = part.match(/^\[(\d+(?:\s*[,–-]\s*\d+)*)\]$/);
    if (cite) {
      const numbers = cite[1].split(/\s*[,–-]\s*/).map(Number);
      return (
        <span key={i} className="whitespace-nowrap">
          {numbers.map((n) => (
            <button key={n} type="button" className="mx-px rounded bg-secondary-100 px-1 text-xs text-secondary-700 hover:underline" onClick={() => onCite?.(n)} data-testid="cite">
              {n}
            </button>
          ))}
        </span>
      );
    }
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (link) {
      return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" className="text-primary underline">{link[1]}</a>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

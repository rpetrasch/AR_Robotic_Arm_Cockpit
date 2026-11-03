"use client";

import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint";
import { validateScenario } from "@/lib/api/scenarios";

/**
 * The YAML pane (docs/specs/spec_cockpit.md §12.3): CodeMirror 6 with YAML highlighting; the backend's
 * validation (the same schema and checks as saving) underlines the problem lines as you type.
 */
export function YamlEditor({ value, onChange, dark }: { value: string; onChange: (text: string) => void; dark: boolean }) {
  const extensions = useMemo(
    () => [
      yaml(),
      lintGutter(),
      linter(
        async (view) => {
          const text = view.state.doc.toString();
          try {
            const res = await validateScenario(text);
            return [...res.errors, ...res.warnings].map((p): Diagnostic => {
              const line = view.state.doc.line(Math.min(Math.max(p.line ?? 1, 1), view.state.doc.lines));
              return {
                from: line.from,
                to: line.to,
                severity: p.severity === "error" ? "error" : "warning",
                message: `${p.path ? `${p.path}: ` : ""}${p.message}`,
              };
            });
          } catch {
            return [];
          }
        },
        { delay: 500 },
      ),
    ],
    [],
  );
  return (
    <div className="overflow-hidden rounded-lg border border-default-200 text-sm" data-testid="yaml-editor">
      <CodeMirror value={value} height="60vh" theme={dark ? "dark" : "light"} extensions={extensions} onChange={onChange} />
    </div>
  );
}

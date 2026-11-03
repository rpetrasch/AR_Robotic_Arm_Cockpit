"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { AskContext, Task } from "@/lib/api/assistant";

/**
 * The assistant drawer's state, shared by the shell and the pages (docs/specs/spec_cockpit.md
 * §14.1 entry points): a page calls `ask()` with a prepared question and its context; the drawer
 * opens and sends it. The scenario editor registers itself as the target of "Apply to editor".
 */
export interface AskRequest {
  id: number;
  question: string;
  task: Task;
  context: AskContext;
  /** start a new thread (the entry points do; typing in the drawer continues the thread) */
  newThread: boolean;
}

interface AssistantState {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  ask: (req: Omit<AskRequest, "id" | "newThread"> & { newThread?: boolean }) => void;
  pending: AskRequest | null;
  takePending: () => AskRequest | null;
  scenarioTarget: ((yaml: string) => void) | null;
  setScenarioTarget: (fn: ((yaml: string) => void) | null) => void;
  /** what the open page is about (a job, a scenario): sent with questions typed in the drawer */
  pageContext: AskContext;
  setPageContext: (ctx: AskContext) => void;
}

const Ctx = createContext<AssistantState | null>(null);

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<AskRequest | null>(null);
  const [scenarioTarget, setTarget] = useState<((yaml: string) => void) | null>(null);
  const [pageContext, setPageContext] = useState<AskContext>({});
  const next = useRef(1);
  const ask = useCallback<AssistantState["ask"]>((req) => {
    setPending({ ...req, newThread: req.newThread ?? true, id: next.current++ });
    setOpen(true);
  }, []);
  const takePending = useCallback(() => {
    const p = pending;
    setPending(null);
    return p;
  }, [pending]);
  const value = useMemo<AssistantState>(
    () => ({
      open,
      setOpen,
      toggle: () => setOpen((o) => !o),
      ask,
      pending,
      takePending,
      scenarioTarget,
      // a function in state must be wrapped, or React calls it as an updater
      setScenarioTarget: (fn) => setTarget(() => fn),
      pageContext,
      setPageContext,
    }),
    [open, ask, pending, takePending, scenarioTarget, pageContext],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssistant(): AssistantState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAssistant outside AssistantProvider");
  return ctx;
}

/** The scenario editor: suggestions from "Help me write this scenario" can be applied to it. */
export function useScenarioTarget(apply: (yaml: string) => void) {
  const { setScenarioTarget } = useAssistant();
  const ref = useRef(apply);
  ref.current = apply;
  useEffect(() => {
    setScenarioTarget((yaml) => ref.current(yaml));
    return () => setScenarioTarget(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- register once per mount
  }, []);
}

/** The page's context for questions typed in the drawer while it is open; cleared on leave. */
export function usePageContext(ctx: AskContext) {
  const { setPageContext } = useAssistant();
  const key = JSON.stringify(ctx);
  useEffect(() => {
    setPageContext(JSON.parse(key));
    return () => setPageContext({});
  }, [key, setPageContext]);
}

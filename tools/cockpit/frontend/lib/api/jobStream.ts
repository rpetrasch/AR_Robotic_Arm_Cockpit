"use client";

import { useState } from "react";
import type { Job, JobLine } from "./cockpit";
import { useSocket } from "./socket";

/** A job and its log, live over /ws/jobs/<id> (backlog first, then new lines). */
export function useJobStream(id: string | null): { job: Job | null; lines: JobLine[] } {
  const [job, setJob] = useState<Job | null>(null);
  const [lines, setLines] = useState<JobLine[]>([]);
  const [current, setCurrent] = useState(id);
  if (current !== id) {
    // another job: start over (state reset during render, React's documented pattern)
    setCurrent(id);
    setJob(null);
    setLines([]);
  }
  useSocket(
    id ? `/ws/jobs/${id}` : null,
    (msg) => {
      if (msg.type === "job") setJob(msg.job);
      if (msg.type === "lines") {
        setLines((prev) => {
          const last = prev.length ? prev[prev.length - 1].seq : 0;
          const fresh = (msg.lines as JobLine[]).filter((l) => l.seq > last);
          return fresh.length ? [...prev, ...fresh] : prev;
        });
      }
    },
    () => setLines([]), // a reconnect resends the whole log
  );
  return { job, lines };
}

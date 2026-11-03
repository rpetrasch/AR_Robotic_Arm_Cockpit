"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { addToast } from "@heroui/react";
import type { Status } from "./cockpit";
import type { Alert } from "./monitoring";

/**
 * Live header status over /ws/status (docs/specs/spec_cockpit.md §7.2). Reconnects with backoff;
 * `down` turns true when the socket has been unable to reconnect for 5 s, which shows the
 * "backend unreachable" page (§8.1). New error / critical alerts (§10.5) arrive on the same
 * socket: a toast, and a browser notification when the user allowed them (Monitoring → Alerts).
 */

export const NOTIFY_KEY = "ar3cockpit.notifications";

function announce(alert: Alert) {
  if (alert.severity !== "error" && alert.severity !== "critical") return;
  addToast({
    title: alert.severity === "critical" ? `CRITICAL: ${alert.message}` : alert.message,
    description: `${alert.rule}${alert.subject ? ` · ${alert.subject}` : ""}`,
    color: "danger",
    timeout: alert.severity === "critical" ? 15000 : 8000,
  });
  try {
    if (localStorage.getItem(NOTIFY_KEY) === "1" && "Notification" in window && Notification.permission === "granted") {
      new Notification(`AR3 Cockpit: ${alert.message}`, { body: alert.rule, tag: `alert-${alert.id}` });
    }
  } catch {
    // storage or notifications blocked: the toast is enough
  }
}
interface StatusState {
  status: Status | null;
  connected: boolean;
  down: boolean;
}

const StatusContext = createContext<StatusState>({ status: null, connected: false, down: false });

function socketUrl(): string {
  const origin =
    process.env.NEXT_PUBLIC_WS_ORIGIN ||
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
  return `${origin}/ws/status`;
}

export function StatusProvider({ enabled, children }: { enabled: boolean; children: React.ReactNode }) {
  const [state, setState] = useState<StatusState>({ status: null, connected: false, down: false });
  const lostAt = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      ws = new WebSocket(socketUrl());
      ws.onopen = () => {
        attempt = 0;
        lostAt.current = null;
        setState((s) => ({ ...s, connected: true, down: false }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "status") setState((s) => ({ ...s, status: msg.data }));
        else if (msg.type === "alert") announce(msg.alert);
      };
      ws.onclose = (ev) => {
        if (stopped) return;
        if (ev.code === 4401) {
          // the session ended: let the next API call send the browser to the login page
          window.location.assign(`/login/?next=${encodeURIComponent(window.location.pathname)}`);
          return;
        }
        lostAt.current ??= Date.now();
        const down = Date.now() - lostAt.current > 5000;
        setState((s) => ({ ...s, connected: false, down }));
        retry = setTimeout(connect, Math.min(1000 * 2 ** attempt++, 5000));
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [enabled]);

  return <StatusContext.Provider value={state}>{children}</StatusContext.Provider>;
}

export const useStatus = () => useContext(StatusContext);

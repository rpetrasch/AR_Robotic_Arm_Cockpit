"use client";

import { useEffect, useRef } from "react";

/** WebSocket URL on the cockpit's own origin (or NEXT_PUBLIC_WS_ORIGIN in `npm run dev`). */
export function socketUrl(path: string): string {
  const origin =
    process.env.NEXT_PUBLIC_WS_ORIGIN ||
    `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
  return `${origin}${path}`;
}

/**
 * A JSON WebSocket with reconnect (1, 2, 4, 5 s). `onMessage` may change between renders.
 * `onOpen` runs on every (re)connect, e.g. to refetch what may have been missed.
 */
export function useSocket(path: string | null, onMessage: (msg: any) => void, onOpen?: () => void) {
  const handler = useRef(onMessage);
  const opened = useRef(onOpen);
  handler.current = onMessage;
  opened.current = onOpen;
  useEffect(() => {
    if (!path) return;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;
    const connect = () => {
      ws = new WebSocket(socketUrl(path));
      ws.onopen = () => {
        attempt = 0;
        opened.current?.();
      };
      ws.onmessage = (ev) => handler.current(JSON.parse(ev.data));
      ws.onclose = (ev) => {
        if (stopped || ev.code === 4401 || ev.code === 4404) return;
        retry = setTimeout(connect, Math.min(1000 * 2 ** attempt++, 5000));
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [path]);
}

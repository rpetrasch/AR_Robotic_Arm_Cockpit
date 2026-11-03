"use client";

import { useCallback, useEffect, useRef } from "react";

/** Elements that keep their own pointer behaviour: dragging starts only next to them. */
const INTERACTIVE = "input, textarea, select, button, a, label, [role='button']";
/** At least this much of the panel stays inside the window. */
const KEEP_VISIBLE_PX = 48;

/**
 * Lets the login panel be moved with the mouse or a finger (docs/specs/spec_cockpit.md §7.3), so the
 * background can be seen. The offset is written straight to the element's transform (no React
 * render per pointer move) and clamped so that the panel cannot be lost off screen; a double
 * click on an empty spot of the panel puts it back in the middle.
 */
export function useDraggable<T extends HTMLElement>() {
  const node = useRef<T | null>(null);
  const offset = useRef({ x: 0, y: 0 });
  const grab = useRef<{ id: number; px: number; py: number; x: number; y: number; rect: DOMRect } | null>(null);

  const apply = useCallback(() => {
    if (node.current) node.current.style.transform = `translate3d(${offset.current.x}px, ${offset.current.y}px, 0)`;
  }, []);

  const clamp = useCallback((x: number, y: number, rect: DOMRect, base: { x: number; y: number }) => {
    // rect: where the panel was when the drag began, at offset `base`
    const left = rect.left - base.x;
    const top = rect.top - base.y;
    const minX = KEEP_VISIBLE_PX - (left + rect.width);
    const maxX = window.innerWidth - KEEP_VISIBLE_PX - left;
    const minY = -top;
    const maxY = window.innerHeight - KEEP_VISIBLE_PX - top;
    return { x: Math.min(maxX, Math.max(minX, x)), y: Math.min(maxY, Math.max(minY, y)) };
  }, []);

  useEffect(() => {
    const el = node.current;
    if (!el) return;
    const down = (e: PointerEvent) => {
      if (e.button !== 0 || (e.target as Element).closest(INTERACTIVE)) return;
      grab.current = { id: e.pointerId, px: e.clientX, py: e.clientY, ...offset.current, rect: el.getBoundingClientRect() };
      el.setPointerCapture(e.pointerId);
      el.dataset.dragging = "true";
    };
    const move = (e: PointerEvent) => {
      const g = grab.current;
      if (!g || e.pointerId !== g.id) return;
      offset.current = clamp(g.x + e.clientX - g.px, g.y + e.clientY - g.py, g.rect, { x: g.x, y: g.y });
      apply();
    };
    const up = (e: PointerEvent) => {
      if (grab.current?.id !== e.pointerId) return;
      grab.current = null;
      delete el.dataset.dragging;
    };
    const recentre = (e: MouseEvent) => {
      if ((e.target as Element).closest(INTERACTIVE)) return;
      offset.current = { x: 0, y: 0 };
      apply();
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("dblclick", recentre);
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      el.removeEventListener("dblclick", recentre);
    };
  }, [apply, clamp]);

  return node;
}

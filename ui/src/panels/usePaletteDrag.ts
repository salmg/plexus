/**
 * Dragging a palette item onto the canvas with a finger.
 *
 * The browser's native drag-and-drop — what the palette uses for a mouse —
 * never fires on touch, so on a phone the only way to add a node was a tap,
 * which drops it at the centre. This adds the drag: press and hold an item,
 * then move, and a ghost follows the finger onto the canvas.
 *
 * Long-press to arm on purpose. A touch that moves straight away is the list
 * being scrolled; only a press that stays put briefly becomes a drag. That is
 * what keeps the palette scrollable and draggable with the same finger, which a
 * move-to-drag threshold cannot: a scroll and a drag both start by moving.
 *
 * Mouse falls through untouched — `pointerType` gates every path here — so the
 * native drag keeps working on the desktop exactly as before.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { usePlacement } from "../canvas/placement";

/** Hold this long without moving to turn a press into a drag. */
const ARM_MS = 240;
/** A move longer than this before arming is a scroll; abandon the drag. */
const SCROLL_SLOP = 10;

export interface DragGhost {
  key: string;
  x: number;
  y: number;
  /** Over the canvas — a release here drops; elsewhere it does nothing. */
  valid: boolean;
}

export function usePaletteDrag() {
  const place = usePlacement((s) => s.place);
  const canvasRect = usePlacement((s) => s.canvasRect);

  const [ghost, setGhost] = useState<DragGhost | null>(null);

  const drag = useRef<{
    key: string;
    x: number;
    y: number;
    armed: boolean;
    timer: ReturnType<typeof setTimeout> | null;
  } | null>(null);
  // A drag ends with a synthetic click on the item; this swallows the one that
  // would otherwise also drop a node at the centre.
  const swallowClick = useRef(false);

  const over = useCallback(
    (x: number, y: number): boolean => {
      const r = canvasRect?.();
      return !!r && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
    },
    [canvasRect],
  );

  useEffect(() => {
    const cancel = () => {
      if (drag.current?.timer) clearTimeout(drag.current.timer);
      drag.current = null;
      setGhost(null);
    };

    const move = (e: PointerEvent) => {
      const d = drag.current;
      if (!d) return;

      if (!d.armed) {
        // Moved before the hold completed — treat it as a scroll, not a drag.
        if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > SCROLL_SLOP) cancel();
        return;
      }

      // Armed: the finger owns the drag now, so keep the page from scrolling.
      e.preventDefault();
      setGhost({ key: d.key, x: e.clientX, y: e.clientY, valid: over(e.clientX, e.clientY) });
    };

    const up = (e: PointerEvent) => {
      const d = drag.current;
      if (d?.timer) clearTimeout(d.timer);
      if (d?.armed) {
        if (over(e.clientX, e.clientY)) place?.(d.key, { x: e.clientX, y: e.clientY });
        swallowClick.current = true;
      }
      drag.current = null;
      setGhost(null);
    };

    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
    };
  }, [over, place]);

  const onPointerDown = useCallback((e: React.PointerEvent, key: string) => {
    if (e.pointerType === "mouse") return; // the mouse uses native drag-and-drop
    const x = e.clientX;
    const y = e.clientY;
    const timer = setTimeout(() => {
      if (!drag.current) return;
      drag.current.armed = true;
      setGhost({ key, x, y, valid: false });
      // A small confirmation the drag has taken hold, where the device offers it.
      navigator.vibrate?.(8);
    }, ARM_MS);
    drag.current = { key, x, y, armed: false, timer };
  }, []);

  // The palette calls this in its onClick to drop the click that trails a drag.
  const consumeClick = useCallback((): boolean => {
    if (!swallowClick.current) return false;
    swallowClick.current = false;
    return true;
  }, []);

  return { onPointerDown, consumeClick, ghost };
}

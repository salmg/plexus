/**
 * The right-click menu.
 *
 * Data-driven on purpose: the canvas decides *what* the menu says for a node, a
 * multi-selection, or bare pane, and this file only knows how to lay a list of
 * items out, open one submenu, clamp to the viewport, and get out of the way.
 * Adding an action is a line in `Canvas.tsx`, not a change here.
 *
 * It opens from the native `contextmenu` event, which is a right-click with a
 * mouse and a long-press on touch — so the same menu reaches a phone without a
 * second gesture to wire up.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

export interface MenuItem {
  key: string;
  label?: string;
  hint?: string;
  onSelect?: () => void;
  disabled?: boolean;
  danger?: boolean;
  separator?: boolean;
  submenu?: MenuItem[];
}

export interface MenuState {
  x: number;
  y: number;
  items: MenuItem[];
}

/** Keep a menu of `size` on screen when it opens near an edge. */
function clamp(at: number, size: number, limit: number): number {
  if (at + size <= limit - 8) return at;
  return Math.max(8, limit - size - 8);
}

export function ContextMenu({
  state,
  onClose,
}: {
  state: MenuState;
  onClose: () => void;
}) {
  const root = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ x: state.x, y: state.y, ready: false });
  const [openSub, setOpenSub] = useState<string | null>(null);

  // Measure once, then place: a menu opened near the right or bottom edge would
  // otherwise spill off the screen it was meant to sit on.
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setPos({
      x: clamp(state.x, r.width, window.innerWidth),
      y: clamp(state.y, r.height, window.innerHeight),
      ready: true,
    });
  }, [state.x, state.y]);

  useEffect(() => {
    root.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
  }, []);

  // Anything that is not choosing from the menu closes it: a click elsewhere, a
  // key, a scroll, a resize. A pointerdown rather than a click so it beats the
  // canvas's own pointer handling to the punch.
  useEffect(() => {
    const outside = (e: Event) => {
      if (!root.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onClose);
    // `true`: catch the scroll of any pane, not just the document.
    window.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);

  const choose = (item: MenuItem) => {
    if (item.disabled || item.submenu) return;
    onClose();
    item.onSelect?.();
  };

  const renderItems = (items: MenuItem[], sub: boolean) =>
    items.map((item) => {
      if (item.separator) return <div key={item.key} className="menu__sep" role="separator" />;
      const hasSub = !!item.submenu?.length;
      return (
        <div
          key={item.key}
          className="menu__row"
          onMouseEnter={() => !sub && setOpenSub(hasSub ? item.key : null)}
        >
          <button
            type="button"
            className={`menu__item${item.danger ? " menu__item--danger" : ""}${
              hasSub ? " menu__item--parent" : ""
            }`}
            disabled={item.disabled}
            aria-haspopup={hasSub || undefined}
            aria-expanded={hasSub ? openSub === item.key : undefined}
            onClick={() => (hasSub ? setOpenSub((k) => (k === item.key ? null : item.key)) : choose(item))}
          >
            <span className="menu__label">{item.label}</span>
            {item.hint && <span className="menu__hint">{item.hint}</span>}
            {hasSub && <span className="menu__caret" aria-hidden="true">›</span>}
          </button>
          {hasSub && openSub === item.key && (
            <div className="menu menu--sub" role="menu">
              {renderItems(item.submenu!, true)}
            </div>
          )}
        </div>
      );
    });

  return (
    <div
      ref={root}
      className="menu"
      role="menu"
      style={{
        left: pos.x,
        top: pos.y,
        visibility: pos.ready ? "visible" : "hidden",
      }}
    >
      {renderItems(state.items, false)}
    </div>
  );
}

/**
 * Which panels are open, and whether they can afford to sit beside the canvas.
 *
 * Three panels surround the work: the palette on the left, the inspector on the
 * right, the console below. On a desktop they are columns and rows of one grid.
 * There is not room for that on a tablet, and nothing like room on a phone —
 * measured at 375px, the palette alone took 216 of them and the canvas was an
 * unreadable strip off the side of the screen.
 *
 * So below a width the same panels become **drawers over the canvas**. The
 * canvas keeps the full viewport underneath, which matters for more than looks:
 * React Flow lays out against its container, and a canvas that changes width
 * every time a panel opens re-fits the graph under the reader.
 *
 * A store rather than App state because the top bar toggles all three and the
 * panels themselves close in `compact`, and threading five more props through
 * two components to say "open" is how a layout gets out of step with itself.
 */

import { useEffect } from "react";
import { create } from "zustand";

import { remember, remembered } from "./remember";

export type Panel = "palette" | "inspector" | "console";

const KEYS: Record<Panel, string> = {
  palette: "plexus.palette-open",
  inspector: "plexus.inspector-open",
  console: "plexus.console-open",
};

/** Where a dragged console height is remembered. */
const CONSOLE_HEIGHT_KEY = "plexus.console-height";

/**
 * How small and how large the console may be dragged, in pixels.
 *
 * The floor keeps a prompt and a line or two visible — smaller than that and
 * the terminal is a slit. The ceiling is most of the viewport rather than all
 * of it, so the canvas never disappears entirely behind the console.
 */
export const CONSOLE_MIN_HEIGHT = 96;

function consoleMaxHeight(): number {
  if (typeof window === "undefined") return 800;
  return Math.max(CONSOLE_MIN_HEIGHT, Math.round(window.innerHeight * 0.8));
}

function clampConsole(px: number): number {
  return Math.min(consoleMaxHeight(), Math.max(CONSOLE_MIN_HEIGHT, Math.round(px)));
}

/** The remembered console height, or null to fall back to the CSS default. */
function savedConsoleHeight(): number | null {
  const value = remembered(CONSOLE_HEIGHT_KEY);
  if (!value) return null;
  const n = Number(value);
  return Number.isFinite(n) ? clampConsole(n) : null;
}

/**
 * The two widths where the layout changes its mind, and why they differ.
 *
 * A tablet is not a big phone. At 768 the palette is 186px and leaves the
 * canvas 582 — narrow, but you add nodes constantly and a palette you have to
 * summon each time is worse than a narrow canvas. A third column at that width
 * is not arguable: 186 + 284 leaves about 300px of canvas.
 *
 * So the inspector overlays from 1100 down, and the palette and console stay
 * docked until 760, below which the canvas is the only thing worth showing
 * without being asked for.
 */
export const COMPACT_WIDTH = 760;

/** Where the inspector stops being a column and starts being a drawer. */
export const INSPECTOR_MIN_WIDTH = 1100;

/**
 * Below this the top bar's controls fold into one menu button.
 *
 * Measured, not chosen: with the graph name and the tagline already hidden,
 * the full set of controls wants 1054px in Spanish. Under that the bar wraps
 * to a second row — which is a safety net worth keeping for a language longer
 * than either of these, but it moves Run off the right-hand end, and Run is
 * the control this bar is arranged around.
 */
export const NARROW_WIDTH = 1060;

function saved(panel: Panel, fallback: boolean): boolean {
  const value = remembered(KEYS[panel]);
  if (value === "1") return true;
  if (value === "0") return false;
  return fallback;
}

/**
 * The same width CSS is looking at.
 *
 * Not `window.innerWidth`, which is a different number: measured in an
 * emulated 375px viewport, `innerWidth` reported **470** while the media
 * queries laid out at 375. A real phone can do the same thing once the visual
 * viewport is pinched away from the layout viewport. Two breakpoints that
 * disagree mean the CSS positions a drawer the JS has not opened, so both
 * sides ask the same question here.
 */
function under(px: number): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia(`(max-width: ${px - 1}px)`).matches;
}

export interface LayoutState {
  palette: boolean;
  inspector: boolean;
  console: boolean;

  /** The palette and the console overlay the canvas rather than sitting beside it. */
  compact: boolean;
  /** The inspector does. True whenever `compact` is. */
  inspectorOverlay: boolean;
  /** No room for the minimap, the graph name, or a full top bar. */
  narrow: boolean;

  /** The user has taken over the inspector; stop opening and closing it for them. */
  pinned: boolean;

  /**
   * The console's height in pixels when the user has dragged it, or null to use
   * the CSS default. Only applies where the console is docked (not compact,
   * where it is a bottom sheet with its own height).
   */
  consoleHeight: number | null;
  /** Everything hidden at once, and what to put back when it is not. */
  focus: boolean;
  /** Hide all three, or restore whatever was open before. */
  toggleFocus: () => void;

  toggle: (panel: Panel) => void;
  close: (panel: Panel) => void;
  closeDrawers: () => void;
  setConsoleHeight: (px: number) => void;
  measure: () => void;
}

/** What was open before focus mode hid it. Not state: nothing renders it. */
let _before: { palette: boolean; inspector: boolean; console: boolean } | null = null;

export const useLayout = create<LayoutState>((set, get) => ({
  // Read where the store is created, not in an effect afterwards — an effect
  // races the one that persists them, and the default wins often enough that
  // a closed console comes back on reload. See `remember.ts`.
  palette: !under(COMPACT_WIDTH) && saved("palette", true),
  inspector: !under(INSPECTOR_MIN_WIDTH) && saved("inspector", true),
  console: !under(COMPACT_WIDTH) && saved("console", true),

  compact: under(COMPACT_WIDTH),
  inspectorOverlay: under(INSPECTOR_MIN_WIDTH),
  narrow: under(NARROW_WIDTH),
  pinned: false,
  consoleHeight: savedConsoleHeight(),
  focus: false,

  toggleFocus: () => {
    const { focus, palette, inspector, console: consoleOpen } = get();
    if (!focus) {
      // Remembered rather than re-derived: coming back should give you the
      // panels you had, not the ones a width rule would have chosen.
      _before = { palette, inspector, console: consoleOpen };
      set({ focus: true, palette: false, inspector: false, console: false });
      return;
    }
    const back = _before ?? { palette: true, inspector: false, console: true };
    _before = null;
    set({ focus: false, ...back });
  },

  toggle: (panel) => {
    const open = !get()[panel];
    remember(KEYS[panel], open ? "1" : "0");

    // One drawer at a time when they overlay: two of them half-covering the
    // canvas leaves nothing to look at, and the backdrop belongs to one.
    if (open && get().compact) {
      set({ palette: false, inspector: false, console: false });
    }
    set({
      focus: false,
      ...(panel === "inspector" ? { inspector: open, pinned: true } : { [panel]: open }),
    });
  },

  close: (panel) => {
    remember(KEYS[panel], "0");
    set(panel === "inspector" ? { inspector: false, pinned: true } : { [panel]: false });
  },

  closeDrawers: () => {
    const { compact, inspectorOverlay } = get();
    if (compact) set({ palette: false, inspector: false, console: false });
    else if (inspectorOverlay) set({ inspector: false });
  },

  setConsoleHeight: (px) => {
    const height = clampConsole(px);
    remember(CONSOLE_HEIGHT_KEY, String(height));
    set({ consoleHeight: height });
  },

  measure: () => {
    const compact = under(COMPACT_WIDTH);
    const roomForInspector = !under(INSPECTOR_MIN_WIDTH);
    const was = get().compact;

    const next: Partial<LayoutState> = {
      compact,
      inspectorOverlay: !roomForInspector,
      narrow: under(NARROW_WIDTH),
    };

    // Crossing into drawer territory closes what was open, so the canvas is
    // not buried the moment a window is made small. Crossing back out restores
    // what the person had chosen, which is what the keys are for.
    if (compact && !was) {
      next.palette = false;
      next.inspector = false;
      next.console = false;
    } else if (!compact && was) {
      next.palette = saved("palette", true);
      next.console = saved("console", true);
      next.inspector = roomForInspector && saved("inspector", true);
    } else if (!compact && !get().pinned) {
      // Following the window, until the user takes the inspector over.
      next.inspector = roomForInspector && saved("inspector", true);
    }

    set(next);
  },
}));

/**
 * Keep the layout in step with the window.
 *
 * The media queries themselves are what is watched, not a `resize` event: they
 * are the same three questions `under()` asks, so the thing that re-measures is
 * the thing that changed. A `resize` listener stays alongside them for the case
 * where a breakpoint is not crossed but the window still moved — and because it
 * costs one line to be sure.
 */
export function useMeasureLayout(): void {
  const measure = useLayout((s) => s.measure);

  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);

    const queries =
      typeof window.matchMedia === "function"
        ? [COMPACT_WIDTH, INSPECTOR_MIN_WIDTH, NARROW_WIDTH].map((px) =>
            window.matchMedia(`(max-width: ${px - 1}px)`),
          )
        : [];
    for (const query of queries) query.addEventListener("change", measure);

    return () => {
      window.removeEventListener("resize", measure);
      for (const query of queries) query.removeEventListener("change", measure);
    };
  }, [measure]);
}

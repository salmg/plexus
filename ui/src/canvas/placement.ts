/**
 * One way to drop a node onto the canvas, wherever the ask comes from.
 *
 * The palette lives outside `<ReactFlowProvider>`, so it cannot ask React Flow
 * where a screen point falls in the panned, zoomed graph. The canvas can, and
 * registers that here: a click, a desktop drag and a touch drag all call the
 * same `place`, so all three land in the same coordinate space and a node
 * dropped after panning is not off the side of the screen.
 */

import { create } from "zustand";

/** Where a dropped node should land: a screen point, or the canvas centre. */
export type DropTarget = { x: number; y: number } | "center";

interface PlacementState {
  /**
   * Add a node of `typeKey` at a screen point (or the visible centre), in the
   * current viewport. Null until the canvas has mounted and registered it.
   */
  place: ((typeKey: string, at: DropTarget) => void) | null;
  /** The canvas's screen rectangle — the drop zone a touch drag tests against. */
  canvasRect: (() => DOMRect | null) | null;
  register: (
    place: PlacementState["place"],
    canvasRect: PlacementState["canvasRect"],
  ) => void;
}

export const usePlacement = create<PlacementState>((set) => ({
  place: null,
  canvasRect: null,
  register: (place, canvasRect) => set({ place, canvasRect }),
}));

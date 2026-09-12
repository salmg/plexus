/**
 * The document store: the graph, what is selected, and undo.
 *
 * Node ids are readable rather than random (`acr122u-target-1`, not a nanoid)
 * because they are what you see in the saved file and type into the console.
 */

import { create } from "zustand";

import { defaultConfig, requireNodeType } from "../catalogue";
import { emptyGraph } from "../graph/types";
import { remembered, THEME_KEY } from "./remember";
import type {
  Device,
  PlexusGraph,
  PlexusNode,
  PortRef,
} from "../graph/types";

const HISTORY_LIMIT = 60;

export type Theme = "dark" | "light";

interface GraphState {
  graph: PlexusGraph;
  /**
   * Selected node ids, in the order they were picked.  A list rather than one
   * id because a rig is built and rearranged in groups: box three nodes, drag
   * them somewhere sensible, delete the two that were a false start.
   */
  selection: string[];
  devices: Device[];
  theme: Theme;
  dirty: boolean;

  past: PlexusGraph[];
  future: PlexusGraph[];

  setGraph: (graph: PlexusGraph, opts?: { clean?: boolean }) => void;
  setName: (name: string) => void;
  setDevices: (devices: Device[]) => void;
  setTheme: (theme: Theme) => void;
  select: (id: string | null) => void;
  /** Apply React Flow's per-node select changes, keeping pick order. */
  applySelection: (changes: { id: string; selected: boolean }[]) => void;
  selectAll: () => void;

  /** The graph as it was when the current drag began. See `moveNode`. */
  dragFrom: PlexusGraph | null;

  addNode: (typeKey: string, at: { x: number; y: number }) => string;
  moveNode: (id: string, at: { x: number; y: number }, commit: boolean) => void;
  updateConfig: (id: string, key: string, value: unknown) => void;
  renameNode: (id: string, label: string) => void;
  setNodeHost: (id: string, host: string) => void;
  toggleDisabled: (id: string) => void;
  removeNode: (id: string) => void;
  removeNodes: (ids: string[]) => void;
  nudge: (ids: string[], dx: number, dy: number) => void;

  connect: (from: PortRef, to: PortRef) => void;
  disconnect: (edgeId: string) => void;

  undo: () => void;
  redo: () => void;
  markClean: () => void;
}

function nextId(graph: PlexusGraph, typeKey: string): string {
  const stem = typeKey.replace(/[._]/g, "-");
  let n = 1;
  while (graph.nodes.some((node) => node.id === `${stem}-${n}`)) n += 1;
  return `${stem}-${n}`;
}

export const useGraph = create<GraphState>((set, get) => {
  /** Apply a change to the graph, pushing the previous version onto history. */
  const edit = (fn: (g: PlexusGraph) => PlexusGraph) =>
    set((state) => ({
      graph: fn(state.graph),
      past: [...state.past, state.graph].slice(-HISTORY_LIMIT),
      future: [],
      dirty: true,
    }));

  return {
    graph: emptyGraph(),
    selection: [],
    dragFrom: null,
    devices: [],
    // Read here rather than restored by an effect: an effect raced the one
    // that persists it, and the default overwrote the saved value often
    // enough that choosing light mode did not survive a reload.
    theme: remembered(THEME_KEY) === "light" ? "light" : "dark",
    dirty: false,
    past: [],
    future: [],

    setGraph: (graph, opts) =>
      set({
        graph,
        selection: [],
        past: [],
        future: [],
        dirty: opts?.clean ? false : true,
      }),

    setName: (name) => edit((g) => ({ ...g, name })),
    setDevices: (devices) => set({ devices }),
    setTheme: (theme) => set({ theme }),
    select: (id) => set({ selection: id === null ? [] : [id] }),

    applySelection: (changes) =>
      set((state) => {
        const next = state.selection.filter(
          (id) => !changes.some((c) => c.id === id && !c.selected),
        );
        for (const change of changes) {
          if (change.selected && !next.includes(change.id)) next.push(change.id);
        }
        // Same members in the same order means nothing to publish; without this
        // every pointer move over the canvas would re-render every node.
        const unchanged =
          next.length === state.selection.length &&
          next.every((id, i) => state.selection[i] === id);
        return unchanged ? state : { selection: next };
      }),

    selectAll: () =>
      set((state) => ({ selection: state.graph.nodes.map((n) => n.id) })),

    addNode: (typeKey, at) => {
      const type = requireNodeType(typeKey);
      const id = nextId(get().graph, typeKey);
      const node: PlexusNode = {
        id,
        type: typeKey,
        position: at,
        config: defaultConfig(type),
      };
      edit((g) => ({ ...g, nodes: [...g.nodes, node] }));
      set({ selection: [id] });
      return id;
    },

    // Dragging fires continuously; only the drag's end is worth an undo entry.
    //
    // Which is why it cannot use `edit`: that pushes `state.graph`, and by the
    // time a drag ends `state.graph` is the *moved* graph — every intermediate
    // position having already been written straight in. Undo restored the
    // position the node had just been dragged to, so dragging was not undoable
    // at all. `dragFrom` remembers where things were when the drag started.
    moveNode: (id, at, commit) => {
      const apply = (g: PlexusGraph): PlexusGraph => ({
        ...g,
        nodes: g.nodes.map((n) => (n.id === id ? { ...n, position: at } : n)),
      });

      if (!commit) {
        set((state) => ({
          graph: apply(state.graph),
          dragFrom: state.dragFrom ?? state.graph,
          dirty: true,
        }));
        return;
      }

      set((state) => ({
        graph: apply(state.graph),
        // `?? state.graph` for a commit with no drag before it — a click that
        // moved nothing, or a programmatic move.
        past: [...state.past, state.dragFrom ?? state.graph].slice(-HISTORY_LIMIT),
        future: [],
        dragFrom: null,
        dirty: true,
      }));
    },

    updateConfig: (id, key, value) =>
      edit((g) => ({
        ...g,
        nodes: g.nodes.map((n) =>
          n.id === id ? { ...n, config: { ...n.config, [key]: value } } : n,
        ),
      })),

    renameNode: (id, label) =>
      edit((g) => ({
        ...g,
        nodes: g.nodes.map((n) =>
          n.id === id ? { ...n, label: label.trim() || undefined } : n,
        ),
      })),

    setNodeHost: (id, host) =>
      edit((g) => ({
        ...g,
        nodes: g.nodes.map((n) =>
          n.id === id ? { ...n, host: host || undefined } : n,
        ),
      })),

    toggleDisabled: (id) =>
      edit((g) => ({
        ...g,
        nodes: g.nodes.map((n) =>
          n.id === id ? { ...n, disabled: !n.disabled || undefined } : n,
        ),
      })),

    removeNode: (id) => get().removeNodes([id]),

    removeNodes: (ids) => {
      if (ids.length === 0) return;
      const gone = new Set(ids);
      edit((g) => ({
        ...g,
        nodes: g.nodes.filter((n) => !gone.has(n.id)),
        // An edge with one end deleted is not an edge; drop it with the node
        // rather than leaving a wire attached to nothing.
        edges: g.edges.filter(
          (e) => !gone.has(e.from.node) && !gone.has(e.to.node),
        ),
      }));
      set((state) => ({ selection: state.selection.filter((id) => !gone.has(id)) }));
    },

    nudge: (ids, dx, dy) => {
      if (ids.length === 0) return;
      const moving = new Set(ids);
      edit((g) => ({
        ...g,
        nodes: g.nodes.map((n) =>
          moving.has(n.id)
            ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } }
            : n,
        ),
      }));
    },

    connect: (from, to) =>
      edit((g) => ({
        ...g,
        edges: [
          ...g.edges,
          {
            id: `${from.node}:${from.port}->${to.node}:${to.port}`,
            from,
            to,
          },
        ],
      })),

    disconnect: (edgeId) =>
      edit((g) => ({ ...g, edges: g.edges.filter((e) => e.id !== edgeId) })),

    undo: () =>
      set((state) => {
        const previous = state.past.at(-1);
        if (!previous) return state;
        return {
          graph: previous,
          past: state.past.slice(0, -1),
          future: [state.graph, ...state.future].slice(0, HISTORY_LIMIT),
          dirty: true,
        };
      }),

    redo: () =>
      set((state) => {
        const [next, ...rest] = state.future;
        if (!next) return state;
        return {
          graph: next,
          past: [...state.past, state.graph].slice(-HISTORY_LIMIT),
          future: rest,
          dirty: true,
        };
      }),

    markClean: () => set({ dirty: false }),
  };
});

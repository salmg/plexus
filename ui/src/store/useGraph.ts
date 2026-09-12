/**
 * The document store: the graph, what is selected, and undo.
 *
 * Node ids are readable rather than random (`acr122u-target-1`, not a nanoid)
 * because they are what you see in the saved file and type into the console.
 */

import { create } from "zustand";

import { defaultConfig, requireNodeType } from "../catalogue";
import { emptyGraph } from "../graph/types";
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
  selection: string | null;
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

  addNode: (typeKey: string, at: { x: number; y: number }) => string;
  moveNode: (id: string, at: { x: number; y: number }, commit: boolean) => void;
  updateConfig: (id: string, key: string, value: unknown) => void;
  renameNode: (id: string, label: string) => void;
  setNodeHost: (id: string, host: string) => void;
  toggleDisabled: (id: string) => void;
  removeNode: (id: string) => void;

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
    selection: null,
    devices: [],
    theme: "dark",
    dirty: false,
    past: [],
    future: [],

    setGraph: (graph, opts) =>
      set({
        graph,
        selection: null,
        past: [],
        future: [],
        dirty: opts?.clean ? false : true,
      }),

    setName: (name) => edit((g) => ({ ...g, name })),
    setDevices: (devices) => set({ devices }),
    setTheme: (theme) => set({ theme }),
    select: (id) => set({ selection: id }),

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
      set({ selection: id });
      return id;
    },

    // Dragging fires continuously; only the drag's end is worth an undo entry.
    moveNode: (id, at, commit) => {
      const apply = (g: PlexusGraph): PlexusGraph => ({
        ...g,
        nodes: g.nodes.map((n) => (n.id === id ? { ...n, position: at } : n)),
      });
      if (commit) edit(apply);
      else set((state) => ({ graph: apply(state.graph), dirty: true }));
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

    removeNode: (id) => {
      edit((g) => ({
        ...g,
        nodes: g.nodes.filter((n) => n.id !== id),
        edges: g.edges.filter((e) => e.from.node !== id && e.to.node !== id),
      }));
      if (get().selection === id) set({ selection: null });
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

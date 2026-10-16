/**
 * The document store: the graph, what is selected, and undo.
 *
 * Node ids are readable rather than random (`acr122u-target-1`, not a nanoid)
 * because they are what you see in the saved file and type into the console.
 */

import { create } from "zustand";

import { defaultConfig, requireNodeType } from "../catalogue";
import { emptyGraph } from "../graph/types";
import { remember, remembered, THEME_KEY } from "./remember";
import type {
  Device,
  NodePosition,
  PlexusEdge,
  PlexusGraph,
  PlexusNode,
  PortRef,
} from "../graph/types";

const HISTORY_LIMIT = 60;

/** How far a duplicate or a paste-with-no-target lands from its original. */
const CLONE_OFFSET = 28;

/**
 * A copied slice of the graph: the nodes, and only the edges *between* them.
 *
 * An edge with one end outside the selection is not part of what was copied —
 * pasting it would attach a wire to a node that is not there. So copy keeps the
 * internal wiring and drops the rest, the mirror of what `removeNodes` does.
 */
export interface Clipboard {
  nodes: PlexusNode[];
  edges: PlexusEdge[];
}

/** Edges with both ends inside the id set — the wiring that travels with a copy. */
function internalEdges(graph: PlexusGraph, ids: Set<string>): PlexusEdge[] {
  return graph.edges.filter((e) => ids.has(e.from.node) && ids.has(e.to.node));
}

/**
 * Graft a slice of graph onto `g` with fresh ids, offset by `shift`.
 *
 * Ids are reserved as they are minted, against the live graph *and* against the
 * clones already made this call, so duplicating three mutation nodes gives
 * `mutation-2/3/4` rather than three collisions. Internal edges are rewired to
 * the new ids; the config is deep-copied so the clone and the original do not
 * share a mutable object.
 */
function graft(
  g: PlexusGraph,
  slice: Clipboard,
  shift: NodePosition,
): { graph: PlexusGraph; ids: string[] } {
  const taken = new Set(g.nodes.map((n) => n.id));
  const idMap = new Map<string, string>();

  const nodes = slice.nodes.map((n) => {
    const stem = n.type.replace(/[._]/g, "-");
    let k = 1;
    while (taken.has(`${stem}-${k}`)) k += 1;
    const id = `${stem}-${k}`;
    taken.add(id);
    idMap.set(n.id, id);
    return {
      ...n,
      id,
      position: { x: n.position.x + shift.x, y: n.position.y + shift.y },
      config: structuredClone(n.config),
    };
  });

  const edges = slice.edges
    .filter((e) => idMap.has(e.from.node) && idMap.has(e.to.node))
    .map((e) => {
      const from = { node: idMap.get(e.from.node)!, port: e.from.port };
      const to = { node: idMap.get(e.to.node)!, port: e.to.port };
      return { id: `${from.node}:${from.port}->${to.node}:${to.port}`, from, to };
    });

  return {
    graph: { ...g, nodes: [...g.nodes, ...nodes], edges: [...g.edges, ...edges] },
    ids: nodes.map((n) => n.id),
  };
}

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
  /** The runtime's reason `devices` has no reader in it; empty when it has. */
  devicesProblem: string;
  theme: Theme;
  dirty: boolean;

  past: PlexusGraph[];
  future: PlexusGraph[];

  setGraph: (graph: PlexusGraph, opts?: { clean?: boolean }) => void;
  setName: (name: string) => void;
  setDevices: (devices: Device[], problem?: string) => void;
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

  /** The last copy, kept out of undo history — it is not an edit to the graph. */
  clipboard: Clipboard | null;
  /** Clone nodes (and the wiring between them) in place; select the clones. */
  duplicate: (ids: string[]) => string[];
  /** Put a copy of these nodes on the clipboard. Leaves the graph untouched. */
  copy: (ids: string[]) => void;
  /** Drop the clipboard onto the graph — at `at` if given, else offset. */
  paste: (at?: NodePosition) => string[];

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

/** Where the working graph is remembered between visits. */
const GRAPH_KEY = "plexus.graph";

/**
 * The graph left open at the end of the last visit, or null for a clean start.
 *
 * A shape check, not a schema validation: enough to be sure this is a graph and
 * not some other JSON that happens to share the key, so a corrupt or foreign
 * value falls back to the default rather than rendering a broken canvas. An
 * empty graph is a real answer — it is what "New" leaves behind — so it comes
 * back as itself, not as a reason to reopen the example.
 */
export function savedGraph(): PlexusGraph | null {
  const raw = remembered(GRAPH_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      parsed.version === 1 &&
      Array.isArray(parsed.nodes) &&
      Array.isArray(parsed.edges)
    ) {
      return {
        version: 1,
        name: typeof parsed.name === "string" ? parsed.name : "untitled",
        hosts: Array.isArray(parsed.hosts) ? parsed.hosts : [],
        nodes: parsed.nodes,
        edges: parsed.edges,
      };
    }
  } catch {
    // A value that will not parse is one we cannot use; start clean.
  }
  return null;
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
    clipboard: null,
    devices: [],
    devicesProblem: "",
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
    setDevices: (devices, problem) => set({ devices, devicesProblem: problem ?? "" }),
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

    duplicate: (ids) => {
      if (ids.length === 0) return [];
      const want = new Set(ids);
      const g = get().graph;
      const slice: Clipboard = {
        // Graph order, not click order, so a duplicated chain keeps its shape.
        nodes: g.nodes.filter((n) => want.has(n.id)),
        edges: internalEdges(g, want),
      };
      if (slice.nodes.length === 0) return [];

      let ids2: string[] = [];
      edit((cur) => {
        const { graph, ids: made } = graft(cur, slice, {
          x: CLONE_OFFSET,
          y: CLONE_OFFSET,
        });
        ids2 = made;
        return graph;
      });
      set({ selection: ids2 });
      return ids2;
    },

    copy: (ids) => {
      const want = new Set(ids);
      const g = get().graph;
      const nodes = g.nodes.filter((n) => want.has(n.id));
      if (nodes.length === 0) return;
      // Snapshotted now, so a later edit to these nodes does not change what a
      // paste produces. Deep-copied for the same reason.
      set({
        clipboard: structuredClone({ nodes, edges: internalEdges(g, want) }),
      });
    },

    paste: (at) => {
      const slice = get().clipboard;
      if (!slice || slice.nodes.length === 0) return [];

      // Anchor the group's top-left to the drop point when there is one, so a
      // paste from the pane menu lands under the cursor rather than on top of
      // the original. With no point, offset like a duplicate.
      let shift = { x: CLONE_OFFSET, y: CLONE_OFFSET };
      if (at) {
        const minX = Math.min(...slice.nodes.map((n) => n.position.x));
        const minY = Math.min(...slice.nodes.map((n) => n.position.y));
        shift = { x: at.x - minX, y: at.y - minY };
      }

      let ids2: string[] = [];
      edit((cur) => {
        const { graph, ids: made } = graft(cur, slice, shift);
        ids2 = made;
        return graph;
      });
      set({ selection: ids2 });
      return ids2;
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

/*
 * Remember the working graph so a reload reopens it, not the example.
 *
 * A subscription rather than a write in every reducer: the graph is edited from
 * a dozen actions and one place that watches the result cannot be forgotten by
 * the next one added. Only writes when the graph reference actually changes, so
 * selecting a node or opening a panel does not touch storage. `remember` is
 * already safe when storage is not, and stringify cannot fail on a plain graph.
 */
useGraph.subscribe((state, previous) => {
  if (state.graph !== previous.graph) {
    remember(GRAPH_KEY, JSON.stringify(state.graph));
  }
});

/**
 * Everything that is true only while a graph is running.
 *
 * Kept apart from the document store on purpose: telemetry changes many times a
 * second and the graph does not, so they should not share a subscription.
 *
 * The two ring buffers are capped.  A relay left up for an hour is millions of
 * APDUs and the browser is not where that belongs — the runtime writes the full
 * trace to disk, and this holds only what a person can scroll.
 */

import { create } from "zustand";

import type {
  ApduEvent,
  Device,
  EdgeTelemetry,
  LogLine,
  NodeTelemetry,
  PlexusGraph,
  RunState,
} from "../graph/types";
import { MockRuntime } from "../runtime/mock";
import { WebSocketRuntime } from "../runtime/ws";
import type { ConsoleResult, Runtime } from "../runtime/transport";
import { useGraph } from "./useGraph";

/**
 * Which runtime this session talks to.
 *
 * The simulator is the default because it needs nothing, and the editor must be
 * usable on a laptop with no reader in it.  Point at a real one with
 * `?runtime=ws://rig.local:8900/ws`, or bake it in with VITE_PLEXUS_RUNTIME.
 * The top bar always shows which of the two is answering, so a simulated result
 * is never mistaken for a card.
 */
function chooseRuntime(): Runtime {
  const fromQuery = new URLSearchParams(window.location.search).get("runtime");
  const url = fromQuery ?? import.meta.env.VITE_PLEXUS_RUNTIME;
  return url ? new WebSocketRuntime(url) : new MockRuntime();
}

const APDU_LIMIT = 600;
const LOG_LIMIT = 300;

const BLANK: NodeTelemetry = {
  status: "idle",
  apdus: 0,
  errors: 0,
  lastMs: null,
  meanMs: null,
};

const BLANK_EDGE: EdgeTelemetry = {
  apdus: 0,
  lastMs: null,
  bytesOut: 0,
  bytesIn: 0,
};

interface RuntimeState {
  runtime: Runtime;
  connected: boolean;
  state: RunState;
  detail?: string;

  nodes: Record<string, NodeTelemetry>;
  edges: Record<string, EdgeTelemetry>;
  apdus: ApduEvent[];
  logs: LogLine[];

  init: () => Promise<void>;
  run: (graph: PlexusGraph) => Promise<void>;
  stop: () => Promise<void>;
  send: (line: string) => Promise<ConsoleResult>;
  clearTrace: () => void;
}

export const useRuntime = create<RuntimeState>((set, get) => ({
  runtime: chooseRuntime(),
  connected: false,
  state: "stopped",
  nodes: {},
  edges: {},
  apdus: [],
  logs: [],

  init: async () => {
    if (get().connected) return;
    await get().runtime.connect({
      onState: (state, detail) => set({ state, detail }),

      onNode: (id, patch) =>
        set((s) => ({
          nodes: { ...s.nodes, [id]: { ...(s.nodes[id] ?? BLANK), ...patch } },
        })),

      onEdge: (id, patch) =>
        set((s) => ({
          edges: {
            ...s.edges,
            [id]: { ...BLANK_EDGE, ...s.edges[id], ...patch },
          },
        })),

      onApdu: (event) =>
        set((s) => ({ apdus: [...s.apdus, event].slice(-APDU_LIMIT) })),

      onLog: (line) => set((s) => ({ logs: [...s.logs, line].slice(-LOG_LIMIT) })),

      onDevices: (devices: Device[]) => useGraph.getState().setDevices(devices),
    });
    set({ connected: true });
  },

  run: async (graph) => {
    set({ nodes: {}, edges: {}, apdus: [] });
    await get().runtime.arm(graph);
  },

  stop: async () => {
    await get().runtime.stop();
  },

  send: (line) => get().runtime.command(line),

  clearTrace: () => set({ apdus: [], logs: [] }),
}));

export function isRunning(state: RunState): boolean {
  return state === "running" || state === "arming";
}

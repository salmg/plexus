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
import { t } from "../i18n";
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
 *
 * **A runtime URL in the address bar is a link somebody could have sent you.**
 * Whatever is on the other end receives every graph you press Run on — which
 * names your readers and your files — and writes lines into your console. This
 * project already treats a `.plexus.yaml` as untrusted input and shows a sheet
 * before opening one; a link that chooses the runtime deserves the same care,
 * and it accepted anything at all.
 *
 * Loopback is the documented case and is dialled without ceremony. Anything
 * else from the query string is asked about once, and declining leaves you on
 * the simulator. `VITE_PLEXUS_RUNTIME` is not asked about: it was baked in by
 * whoever built this copy, which is a decision already made.
 */
function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host.endsWith(".localhost")
  );
}

/** Was the runtime named by a link or a build, rather than left to us? */
function chosenExplicitly(): boolean {
  return Boolean(
    new URLSearchParams(window.location.search).get("runtime") ??
      import.meta.env.VITE_PLEXUS_RUNTIME,
  );
}

/**
 * A runtime serving this very page.
 *
 * `plexus up` serves the editor and the runtime from one origin, so there is
 * nothing to configure and nothing to paste: if `/api/health` answers here,
 * the socket is here too.
 *
 * Same origin, which is the whole reason this works — the same probe against a
 * runtime on another port is a cross-origin fetch with no CORS behind it, and
 * the browser will not let the page read the reply. Tried once, on connect,
 * and quiet when there is nothing there: an editor opened from a file or from
 * a static host simply stays on the simulator.
 */
async function sameOriginRuntime(): Promise<Runtime | null> {
  if (!window.location.protocol.startsWith("http")) return null;
  try {
    const response = await fetch("api/health", { credentials: "omit" });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null) return null;
    if (typeof (body as { protocol?: unknown }).protocol !== "number") return null;

    const socket = new URL("ws", window.location.href);
    socket.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return new WebSocketRuntime(socket.toString());
  } catch {
    return null;
  }
}

function chooseRuntime(): Runtime {
  const fromQuery = new URLSearchParams(window.location.search).get("runtime");
  const raw = fromQuery ?? import.meta.env.VITE_PLEXUS_RUNTIME;
  if (!raw) return new MockRuntime();

  let url: URL;
  try {
    url = new URL(raw, window.location.href);
  } catch {
    return new MockRuntime();
  }
  // Not a WebSocket URL at all — `javascript:`, `http:`, anything.
  if (url.protocol !== "ws:" && url.protocol !== "wss:") return new MockRuntime();

  if (
    fromQuery &&
    !isLoopbackHost(url.hostname) &&
    !window.confirm(t("runtime.confirmRemote", { host: url.host }))
  ) {
    return new MockRuntime();
  }

  return new WebSocketRuntime(url.toString());
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
  /** How many have ever arrived, which outlives the ring buffer. */
  apduTotal: number;
  logTotal: number;
  /** Node types this runtime cannot bind, keyed to what they need. */
  unavailable: Record<string, string>;

  init: () => Promise<void>;
  run: (graph: PlexusGraph) => Promise<void>;
  stop: () => Promise<void>;
  send: (line: string) => Promise<ConsoleResult>;
  refreshDevices: () => void;
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
  apduTotal: 0,
  logTotal: 0,
  unavailable: {},

  init: async () => {
    if (get().connected) return;

    // Nothing was named, so ask the origin this page came from. `plexus up`
    // is on the other end of that question.
    if (!chosenExplicitly()) {
      const here = await sameOriginRuntime();
      if (here) set({ runtime: here });
    }
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

      // `apduTotal` and `logTotal` count everything that has ever arrived,
      // which is not the same as how much is still held. The console used to
      // track its place with `apdus.length`, and once the ring buffer was full
      // that stopped moving: `slice(cap)` is empty, forever. Measured on the
      // exact shape — 900 events, 600 printed, 300 dropped in silence, and
      // nothing printed again for the life of the page.
      onApdu: (event) =>
        set((s) => ({
          apdus: [...s.apdus, event].slice(-APDU_LIMIT),
          apduTotal: s.apduTotal + 1,
        })),

      onLog: (line) =>
        set((s) => ({
          logs: [...s.logs, line].slice(-LOG_LIMIT),
          logTotal: s.logTotal + 1,
        })),

      onDevices: (devices: Device[], problem?: string) =>
        useGraph.getState().setDevices(devices, problem),

      onUnavailable: (needs) => set({ unavailable: needs }),
    });
    set({ connected: true });
  },

  run: async (graph) => {
    set({ nodes: {}, edges: {}, apdus: [], apduTotal: 0 });
    try {
      await get().runtime.arm(graph);
    } catch (error) {
      // `arm` used to resolve whether or not the frame went anywhere, so
      // pressing Run with the runtime gone showed a run starting with nothing
      // on the other end.
      set({ state: "faulted", detail: message(error) });
      set((s) => ({
        logs: [
          ...s.logs,
          { t: 0, level: "error" as const, source: "plexus", text: message(error) },
        ].slice(-LOG_LIMIT),
      }));
    }
  },

  stop: async () => {
    try {
      await get().runtime.stop();
    } catch (error) {
      set({ state: "stopped", detail: message(error) });
      set((s) => ({
        logs: [
          ...s.logs,
          { t: 0, level: "error" as const, source: "plexus", text: message(error) },
        ].slice(-LOG_LIMIT),
      }));
    }
  },

  send: (line) => get().runtime.command(line),

  refreshDevices: () => get().runtime.refreshDevices(),

  clearTrace: () => set({ apdus: [], logs: [], apduTotal: 0, logTotal: 0 }),
}));

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Is there something to stop?
 *
 * Not the same question as "is a run in progress", which is what this used to
 * answer. A faulted run keeps its card bound on purpose — `_release_if_idle`
 * in the engine leaves the rig up so it can be inspected — and a finished
 * script settles to `running` for the same reason. With `faulted` excluded,
 * the editor showed Run again while the reader was still claimed, and there
 * was no button left that would release it.
 *
 * So: everything except `stopped` has something to stop.
 */
export function isRunning(state: RunState): boolean {
  return state !== "stopped";
}

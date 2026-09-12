/**
 * The runtime contract.
 *
 * Everything the editor knows about a running graph arrives through this
 * interface, and there are two implementations: `MockRuntime`, which invents a
 * plausible transaction so the interface can be built and driven with no
 * hardware, and `WebSocketRuntime`, which talks to the Python process that
 * actually holds the readers.
 *
 * Keeping the mock honest is the point.  It emits the same events in the same
 * order as the real one, so a panel that works against it works against a card.
 */

import type {
  ApduEvent,
  Device,
  EdgeTelemetry,
  LogLine,
  NodeTelemetry,
  PlexusGraph,
  RunState,
} from "../graph/types";

export interface RuntimeHandlers {
  onState: (state: RunState, detail?: string) => void;
  onNode: (nodeId: string, telemetry: Partial<NodeTelemetry>) => void;
  onEdge: (edgeId: string, telemetry: Partial<EdgeTelemetry>) => void;
  onApdu: (event: ApduEvent) => void;
  onLog: (line: LogLine) => void;
  onDevices: (devices: Device[]) => void;
}

export interface ConsoleResult {
  /** Lines to print.  Already formatted; the console does not re-wrap. */
  lines: string[];
  level?: "info" | "error";
}

export interface Runtime {
  /** Human name shown in the top bar, e.g. "simulated" or "rig.local:8900". */
  readonly label: string;

  connect: (handlers: RuntimeHandlers) => Promise<void>;
  disconnect: () => void;

  /** Bind hardware and start the conversation. */
  arm: (graph: PlexusGraph) => Promise<void>;
  stop: () => Promise<void>;

  /** One console line in, zero or more lines out. */
  command: (line: string) => Promise<ConsoleResult>;
}

export function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

export function hex(bytes: number[]): string {
  return bytes.map((b) => b.toString(16).padStart(2, "0").toUpperCase()).join("");
}

/** Parse a hex string, tolerating spaces and colons.  Null when malformed. */
export function parseHex(text: string): number[] | null {
  const clean = text.replace(/[\s:]/g, "");
  if (clean.length === 0 || clean.length % 2 !== 0) return null;
  if (!/^[0-9a-fA-F]+$/.test(clean)) return null;
  const out: number[] = [];
  for (let i = 0; i < clean.length; i += 2) {
    out.push(Number.parseInt(clean.slice(i, i + 2), 16));
  }
  return out;
}

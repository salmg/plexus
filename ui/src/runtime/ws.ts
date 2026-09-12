/**
 * The real runtime, over a WebSocket.
 *
 * The Python process holds the readers, so everything here is a message.  The
 * frame shapes are specified in PROTOCOL.md and mirrored by
 * `plexus/api/protocol.py`; this file and that one are the two ends of the same
 * contract, and neither should grow a field the other has not agreed to.
 *
 * Two things this does that the simulator does not have to:
 *
 *  - It accepts batched frames.  A relay at speed produces more telemetry
 *    than a browser wants as individual messages, so the runtime is free to
 *    coalesce a tick's worth into one `batch`.
 *  - It reconnects.  Losing the socket is not losing the run: the relay keeps
 *    going, and on reconnect the runtime replays current state.
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
import type { ConsoleResult, Runtime, RuntimeHandlers } from "./transport";

export const PROTOCOL_VERSION = 1;

type ServerFrame =
  | { type: "state"; state: RunState; detail?: string }
  | { type: "node"; id: string; telemetry: Partial<NodeTelemetry> }
  | { type: "edge"; id: string; telemetry: Partial<EdgeTelemetry> }
  | { type: "apdu"; event: ApduEvent }
  | { type: "log"; line: LogLine }
  | { type: "devices"; devices: Device[] }
  | { type: "result"; id: string; lines: string[]; level?: "info" | "error" }
  | { type: "error"; message: string }
  | { type: "batch"; frames: ServerFrame[] };

interface Pending {
  resolve: (result: ConsoleResult) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const COMMAND_TIMEOUT_MS = 15_000;
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8_000;

export class WebSocketRuntime implements Runtime {
  readonly label: string;

  private readonly url: string;
  private socket: WebSocket | null = null;
  private handlers: RuntimeHandlers | null = null;
  private pending = new Map<string, Pending>();
  private nextId = 1;
  private closing = false;
  private retries = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(url: string) {
    this.url = url;
    this.label = url.replace(/^wss?:\/\//, "");
  }

  async connect(handlers: RuntimeHandlers): Promise<void> {
    this.handlers = handlers;
    this.closing = false;
    await this.dial();
  }

  disconnect(): void {
    this.closing = true;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.socket?.close();
    this.socket = null;
    this.failPending("Disconnected.");
    this.handlers = null;
  }

  async arm(graph: PlexusGraph): Promise<void> {
    this.send({ type: "arm", graph });
  }

  async stop(): Promise<void> {
    this.send({ type: "stop" });
  }

  command(line: string): Promise<ConsoleResult> {
    const id = `c${this.nextId++}`;

    return new Promise<ConsoleResult>((resolve, reject) => {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        resolve({ lines: ["Not connected to a runtime."], level: "error" });
        return;
      }

      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({
          lines: [`Timed out after ${COMMAND_TIMEOUT_MS / 1000}s with no answer.`],
          level: "error",
        });
      }, COMMAND_TIMEOUT_MS);

      this.pending.set(id, { resolve, reject, timer });
      this.send({ type: "command", id, line });
    });
  }

  /* ── Plumbing ────────────────────────────────────────────────────────── */

  private dial(): Promise<void> {
    return new Promise((resolve) => {
      const socket = new WebSocket(this.url);
      this.socket = socket;

      socket.onopen = () => {
        this.retries = 0;
        this.send({ type: "hello", version: PROTOCOL_VERSION });
        resolve();
      };

      socket.onmessage = (event) => {
        try {
          this.receive(JSON.parse(String(event.data)) as ServerFrame);
        } catch {
          this.handlers?.onLog({
            t: 0,
            level: "error",
            source: "plexus",
            text: "Runtime sent a frame that is not JSON. Ignored.",
          });
        }
      };

      socket.onerror = () => {
        // `onclose` always follows, and carries the retry; nothing to do here
        // beyond not letting the error escape as an unhandled event.
      };

      socket.onclose = () => {
        this.socket = null;
        this.failPending("The runtime connection closed.");
        if (this.closing) return;

        this.handlers?.onState("faulted", "Lost the runtime. Reconnecting.");
        const wait = Math.min(
          RECONNECT_MAX_MS,
          RECONNECT_BASE_MS * 2 ** this.retries++,
        );
        this.reconnectTimer = setTimeout(() => void this.dial(), wait);
        resolve();
      };
    });
  }

  private send(message: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  private receive(frame: ServerFrame): void {
    const handlers = this.handlers;
    if (!handlers) return;

    switch (frame.type) {
      case "batch":
        for (const inner of frame.frames) this.receive(inner);
        return;
      case "state":
        handlers.onState(frame.state, frame.detail);
        return;
      case "node":
        handlers.onNode(frame.id, frame.telemetry);
        return;
      case "edge":
        handlers.onEdge(frame.id, frame.telemetry);
        return;
      case "apdu":
        handlers.onApdu(frame.event);
        return;
      case "log":
        handlers.onLog(frame.line);
        return;
      case "devices":
        handlers.onDevices(frame.devices);
        return;
      case "result": {
        const waiting = this.pending.get(frame.id);
        if (!waiting) return;
        clearTimeout(waiting.timer);
        this.pending.delete(frame.id);
        waiting.resolve({ lines: frame.lines, level: frame.level });
        return;
      }
      case "error":
        handlers.onLog({
          t: 0,
          level: "error",
          source: "runtime",
          text: frame.message,
        });
    }
  }

  private failPending(reason: string): void {
    for (const [, waiting] of this.pending) {
      clearTimeout(waiting.timer);
      waiting.resolve({ lines: [reason], level: "error" });
    }
    this.pending.clear();
  }
}

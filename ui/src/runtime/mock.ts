/**
 * A simulated runtime.
 *
 * There is no card here.  This invents a plausible contactless transaction and
 * pushes it through whatever chain the graph describes, spending each node's
 * declared latency on the way, so the editor can be built and driven on a
 * machine with no reader attached.
 *
 * The APDU script below is illustrative, not a capture.  The structures are
 * shaped like the real thing — a PPSE select, an AID select, GPO, a record
 * read, a cryptogram — so the trace reads correctly to someone who knows EMV,
 * but no card ever emitted these bytes and nothing should be concluded from
 * them.
 *
 * English, deliberately, and the same English the Python runtime uses. This
 * file could translate itself — it runs in the browser — but a simulator that
 * answered `status` in Spanish while the runtime answered in English would
 * stop being the thing a panel can be built against. See docs/languages.md.
 */

import { nodeType } from "../catalogue";
import type { Device, PlexusGraph, PlexusNode } from "../graph/types";
import type { ConsoleResult, Runtime, RuntimeHandlers } from "./transport";
import { now, parseHex } from "./transport";

interface Step {
  command: string;
  response: string;
  note: string;
}

const SCRIPT: Step[] = [
  {
    command: "00A404000E325041592E5359532E444446303100",
    response:
      "6F23840E325041592E5359532E4444463031A511BF0C0E610C4F07A00000000410108701019000",
    note: "SELECT PPSE",
  },
  {
    command: "00A4040007A000000004101000",
    response:
      "6F328407A0000000041010A527500A4D6173746572436172648701019F38129F66049F02069F03069F1A0295055F2A029A039000",
    note: "SELECT AID",
  },
  {
    // 80 A8 00 00 23 | 83 21 <33 bytes of PDOL data> | 00
    // PDOL data:  TTQ(4) amount(6) other(6) country(2) TVR(5)
    //             currency(2) date(3) type(1) unpredictable(4)
    // The amount starts at hex offset GPO_AMOUNT_AT — see mutateGpo.
    command:
      "80A80000238321360040000000000010000000000000000276000000000009782609110034" +
      "12ABCD00",
    response: "771282025C009410080101001801020018010300009000",
    note: "GET PROCESSING OPTIONS",
  },
  {
    command: "00B2010C00",
    response:
      "705A5A08541333001234567F5F24032512315F25031801015F280208405F3401019F0702FF008C159F02069F03069F1A0295055F2A029A039C019F37048E0A00000000000000001E039000",
    note: "READ RECORD 1, SFI 1",
  },
  {
    command: "00B2011400",
    response:
      "70328F01059F3201039224A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4E5F60718293A4B5C6D7E8F909000",
    note: "READ RECORD 1, SFI 2",
  },
  {
    command:
      "80AE80002B0000000001000000000000097800000000000978260911003412ABCD9F3704220000001E0300000000000000",
    response: "771281129F2701809F360200059F2608A1B2C3D4E5F607189000",
    note: "GENERATE AC",
  },
];

const DEVICES: Device[] = [
  {
    id: "acr122u:001",
    kind: "acr122u",
    name: "ACS ACR122U PICC 00",
    host: "",
    available: true,
    detail: "PN532 v1.6",
  },
  {
    id: "acr122u:002",
    kind: "acr122u",
    name: "ACS ACR122U PICC 01",
    host: "",
    available: true,
    detail: "PN532 v1.6",
  },
  {
    id: "pcsc:gemalto",
    kind: "pcsc",
    name: "Gemalto PC Twin Reader",
    host: "",
    available: true,
    detail: "contact",
  },
  {
    id: "simtrace:001",
    kind: "simtrace2",
    name: "SimTrace2",
    host: "",
    available: false,
    detail: "not plugged in",
  },
];

/** One hop of the chain: the node, and the edge that leaves it. */
interface Hop {
  node: PlexusNode;
  edgeId: string | null;
  latency: number;
}

export class MockRuntime implements Runtime {
  readonly label = "simulated";

  private handlers: RuntimeHandlers | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private graph: PlexusGraph | null = null;
  private chain: Hop[] = [];
  private step = 0;
  private seq = 0;
  private startedAt = 0;
  private paused = false;
  private gapMs = 320;
  private counts = new Map<string, { apdus: number; ms: number[] }>();

  async connect(handlers: RuntimeHandlers): Promise<void> {
    this.handlers = handlers;
    handlers.onDevices(DEVICES);
    this.log("info", "plexus", "Simulated runtime. No hardware is bound.");
  }

  disconnect(): void {
    this.clearTimer();
    this.handlers = null;
  }

  async arm(graph: PlexusGraph): Promise<void> {
    this.graph = graph;
    this.chain = buildChain(graph);
    this.step = 0;
    this.seq = 0;
    this.paused = false;
    this.counts.clear();
    this.startedAt = now();

    this.handlers?.onState("arming");

    if (this.chain.length < 2) {
      this.handlers?.onState("faulted", "Nothing to run: the chain is not complete.");
      this.log("error", "plexus", "No complete chain from a terminal to a card.");
      return;
    }

    for (const hop of this.chain) {
      this.handlers?.onNode(hop.node.id, { status: "ready", apdus: 0, errors: 0 });
    }

    this.log(
      "info",
      "plexus",
      `Armed ${this.chain.length} nodes, ${this.chain.reduce((a, h) => a + h.latency, 0)} ms expected per exchange.`,
    );
    this.handlers?.onState("running");
    this.schedule(180);
  }

  async stop(): Promise<void> {
    this.clearTimer();
    this.handlers?.onState("stopping");
    for (const hop of this.chain) {
      this.handlers?.onNode(hop.node.id, { status: "idle" });
    }
    this.log("info", "plexus", "Stopped. Devices released.");
    this.handlers?.onState("stopped");
  }

  async command(line: string): Promise<ConsoleResult> {
    const [verb, ...rest] = line.trim().split(/\s+/);
    const arg = rest.join(" ");

    switch (verb) {
      case "help":
        return {
          lines: [
            "status            what the runtime is doing",
            "nodes             each node, its state and its counters",
            "edges             each edge and what has crossed it",
            "devices           hardware the runtime can see",
            "tx <hex>          push one command APDU through the chain",
            "rate <ms>         gap between transactions (currently " + this.gapMs + ")",
            "pause / resume    hold the traffic without unbinding",
            "clear             clear this console",
          ],
        };

      case "status": {
        const running = this.timer !== null;
        return {
          lines: [
            `runtime   ${this.label}`,
            `state     ${running ? (this.paused ? "paused" : "running") : "stopped"}`,
            `graph     ${this.graph?.name ?? "(none)"}`,
            `chain     ${this.chain.map((h) => h.node.id).join(" -> ") || "(empty)"}`,
            `budget    ${this.chain.reduce((a, h) => a + h.latency, 0)} ms per exchange`,
          ],
        };
      }

      case "nodes":
        if (!this.chain.length) return { lines: ["Nothing armed."] };
        return {
          lines: this.chain.map((hop) => {
            const c = this.counts.get(hop.node.id);
            const mean = c?.ms.length
              ? (c.ms.reduce((a, b) => a + b, 0) / c.ms.length).toFixed(1)
              : "-";
            return `${hop.node.id.padEnd(22)} ${String(c?.apdus ?? 0).padStart(6)} apdu  ${mean.padStart(6)} ms`;
          }),
        };

      case "edges":
        if (!this.graph) return { lines: ["Nothing armed."] };
        return {
          lines: this.graph.edges.map(
            (e) => `${e.from.node}:${e.from.port} -> ${e.to.node}:${e.to.port}`,
          ),
        };

      case "devices":
        return {
          lines: DEVICES.map(
            (d) =>
              `${d.available ? "ok  " : "gone"} ${d.id.padEnd(16)} ${d.name}${d.detail ? "  (" + d.detail + ")" : ""}`,
          ),
        };

      case "tx": {
        const bytes = parseHex(arg);
        if (!bytes) return { lines: [`Not hex: ${arg}`], level: "error" };
        if (!this.chain.length) {
          return { lines: ["Nothing armed. Press Run first."], level: "error" };
        }
        const command = bytes
          .map((b) => b.toString(16).padStart(2, "0").toUpperCase())
          .join("");
        this.exchange(command, "6A82", "console tx");
        return { lines: [`-> ${command}`] };
      }

      case "rate": {
        const ms = Number.parseInt(arg, 10);
        if (!Number.isFinite(ms) || ms < 0 || ms > 60000) {
          return { lines: ["rate takes a gap in milliseconds, 0 to 60000."], level: "error" };
        }
        this.gapMs = ms;
        return { lines: [`Gap between transactions is now ${ms} ms.`] };
      }

      case "pause":
        this.paused = true;
        return { lines: ["Paused. Devices stay bound."] };

      case "resume":
        this.paused = false;
        if (this.timer === null && this.chain.length) this.schedule(0);
        return { lines: ["Resumed."] };

      case "":
        return { lines: [] };

      default:
        return {
          lines: [`Unknown command "${verb}". Try help.`],
          level: "error",
        };
    }
  }

  /* ── Internals ───────────────────────────────────────────────────────── */

  private schedule(ms: number): void {
    this.clearTimer();
    this.timer = setTimeout(() => this.tick(), ms);
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private tick(): void {
    if (!this.handlers || !this.chain.length) return;

    if (this.paused) {
      this.schedule(200);
      return;
    }

    const step = SCRIPT[this.step % SCRIPT.length];
    this.exchange(step.command, step.response, step.note);
    this.step += 1;

    const endOfTransaction = this.step % SCRIPT.length === 0;
    this.schedule(endOfTransaction ? this.gapMs + 400 : this.gapMs);
  }

  /**
   * Push one command down the chain and one response back up it.
   *
   * Every edge gets telemetry, but not every edge gets a trace line: one
   * command crossing four nodes is one exchange, and printing it four times
   * reads as duplicate noise.  What is worth a second line is a transform that
   * changed the bytes — so the trace shows the command as it entered and again
   * as it left anything that rewrote it.
   */
  private exchange(command: string, response: string, note: string): void {
    if (!this.handlers) return;

    const t = now() - this.startedAt;
    const jitter = () => 0.85 + Math.random() * 0.3;

    let elapsed = 0;
    let carried = command;
    let first = true;

    for (const hop of this.chain) {
      const cost = hop.latency * jitter();
      elapsed += cost;

      const record = this.counts.get(hop.node.id) ?? { apdus: 0, ms: [] };
      record.apdus += 1;
      record.ms.push(cost);
      if (record.ms.length > 50) record.ms.shift();
      this.counts.set(hop.node.id, record);

      const mean = record.ms.reduce((a, b) => a + b, 0) / record.ms.length;
      this.handlers.onNode(hop.node.id, {
        status: "live",
        apdus: record.apdus,
        lastMs: Number(cost.toFixed(1)),
        meanMs: Number(mean.toFixed(1)),
      });

      if (!hop.edgeId) continue;

      const rewritten = hop.node.type === "mutation" ? mutateGpo(carried) : null;
      if (first || rewritten) {
        this.handlers.onApdu({
          seq: this.seq++,
          t,
          edge: hop.edgeId,
          dir: "command",
          hex: rewritten ?? carried,
          note,
          ...(rewritten ? { mutatedBy: hop.node.id } : {}),
        });
      }
      if (rewritten) carried = rewritten;
      first = false;

      this.handlers.onEdge(hop.edgeId, {
        apdus: record.apdus,
        lastMs: Number(elapsed.toFixed(1)),
        bytesOut: carried.length / 2,
        bytesIn: response.length / 2,
      });
    }

    const roundTrip = elapsed * 2;
    const lastEdge = [...this.chain].reverse().find((h) => h.edgeId)?.edgeId;
    if (lastEdge) {
      this.handlers.onApdu({
        seq: this.seq++,
        t: t + roundTrip,
        edge: lastEdge,
        dir: "response",
        hex: response,
        ms: Number(roundTrip.toFixed(1)),
        note,
      });
    }
  }

  private log(level: "info" | "warn" | "error", source: string, text: string): void {
    this.handlers?.onLog({ t: now() - this.startedAt, level, source, text });
  }
}

/** Hex offset of the amount inside the GPO above: header(10) + 8321(4) + TTQ(8). */
const GPO_AMOUNT_AT = 22;
const GPO_AMOUNT_LEN = 12;

/**
 * Stand in for what a mutation node does to a GET PROCESSING OPTIONS.
 *
 * The example playbook sets tag 9F02 — the amount — to one cent, so this
 * rewrites the amount field and leaves everything else alone.  It works on a
 * fixed offset because the simulator wrote the command itself and knows where
 * the field is; the real thing parses the PDOL, and that is atrium's mutation
 * engine, in the runtime.  This exists only so the trace shows a before and an
 * after rather than claiming a rewrite that never happened.
 */
function mutateGpo(command: string): string | null {
  if (command.slice(2, 4) !== "A8") return null;
  // A fixed offset needs a command long enough to have something at it.
  // `slice` past the end returns a short string rather than failing, so a
  // GPO shorter than the simulator's own script came back truncated — and
  // was reported as a genuine rewrite, which is the one thing this function
  // exists not to do.
  if (command.length < GPO_AMOUNT_AT + GPO_AMOUNT_LEN) return null;
  const before = command.slice(GPO_AMOUNT_AT, GPO_AMOUNT_AT + GPO_AMOUNT_LEN);
  const after = "000000000001";
  if (before === after) return null;
  return (
    command.slice(0, GPO_AMOUNT_AT) +
    after +
    command.slice(GPO_AMOUNT_AT + GPO_AMOUNT_LEN)
  );
}

/**
 * Walk the graph from every node that only asks, following the edge that
 * leaves each one, until something answers and asks nothing further.
 *
 * Out-of-band edges are skipped: they are a side channel to the card, not a
 * step in the conversation, and counting them would double the latency of a
 * chain that has an injection node in it.
 */
export function buildChain(graph: PlexusGraph): Hop[] {
  const active = graph.nodes.filter((n) => !n.disabled);

  const roots = active.filter((n) => {
    const type = nodeType(n.type);
    if (!type) return false;
    const hasResponder = type.ports.some((p) => p.role === "responder");
    const hasRequester = type.ports.some((p) => p.role === "requester" && !p.oob);
    return hasRequester && !hasResponder;
  });

  const hops: Hop[] = [];
  const seen = new Set<string>();
  let current: PlexusNode | undefined = roots[0];

  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    const edge = graph.edges.find(
      (e) => e.from.node === current!.id && e.from.port !== "oob",
    );
    hops.push({
      node: current,
      edgeId: edge?.id ?? null,
      latency: nodeType(current.type)?.latencyMs ?? 0,
    });
    current = edge ? active.find((n) => n.id === edge.to.node) : undefined;
  }

  return hops;
}

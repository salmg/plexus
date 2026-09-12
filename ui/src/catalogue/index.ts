/**
 * The node catalogue.
 *
 * Every entry maps onto something that already exists in atrium — a
 * `CardTransport` implementation, a `SmartcardOS`, or the `on_command` /
 * `on_response` pair the mutation engine exposes.  Nothing here is aspirational
 * except where a caveat says so.
 */

import type { NodeType } from "./types.ts";
import { CARD_PORT, PASSTHROUGH_PORTS, TERMINAL_PORT } from "./types.ts";

const DEFAULT_PLAYBOOK = `# Mutation rules, in atrium's playbook format.
# Existing playbooks from atrium/playbooks/ paste in here unchanged.
enabled: true
log_mutations: true

pdol_mutations:
  - tag: "9F02"
    value: "000000000001"
    enabled: true
    comment: "Amount: 1 cent"

response_mutations: []
afl_mutations: []
dol_mutations: []
`;

/* ── Terminal side — nodes that ask ────────────────────────────────────── */

const TERMINALS: NodeType[] = [
  {
    key: "simtrace2",
    title: "SimTrace2",
    nodeClass: "terminal",
    summary: "Presents a virtual card to a real terminal over the contact interface.",
    ports: [CARD_PORT],
    device: "simtrace2",
    latencyMs: 2,
    fields: [
      { kind: "device", key: "device", label: "Device", deviceKind: "simtrace2" },
      {
        kind: "bool",
        key: "assertOnly",
        label: "Refuse to start if the daemon is not already up",
        default: true,
        help: "simtrace2-remsim needs raw USB access and is started by hand in a root shell. Plexus never launches it.",
      },
    ],
    caveats: [
      "The daemon runs outside Plexus. This node checks that it is up and binds to it.",
    ],
  },
  {
    key: "acr122u.target",
    title: "ACR122U target",
    nodeClass: "terminal",
    summary: "Emulates a contactless card to a terminal in whose field it sits.",
    ports: [CARD_PORT],
    device: "acr122u",
    latencyMs: 6,
    fields: [
      { kind: "device", key: "device", label: "Reader", deviceKind: "acr122u" },
      {
        kind: "bool",
        key: "splitResponses",
        label: "Offer long responses as 61 XX",
        default: false,
        help: "Lets the terminal collect an oversized response with GET RESPONSE instead of failing the frame.",
      },
    ],
    caveats: [
      "The PN532 forces the first UID byte to 0x08, the random-UID marker. A terminal that pins a UID will not see the card's own.",
      "One frame per exchange: 253 bytes for the relayed response. Longer needs 14443-4 chaining, which is not implemented.",
      "Every APDU costs a USB round trip. Contactless kernels enforce a timing budget and may abandon the transaction.",
    ],
  },
  {
    key: "vpcd",
    title: "Virtual reader",
    nodeClass: "terminal",
    summary: "Presents a card to anything on this machine that speaks PC/SC, via vpcd.",
    ports: [CARD_PORT],
    latencyMs: 1,
    fields: [
      { kind: "text", key: "host", label: "vpcd host", default: "127.0.0.1" },
      { kind: "number", key: "port", label: "vpcd port", default: 35963, min: 1, max: 65535 },
    ],
    caveats: ["Needs virtualsmartcard installed and vpcd loaded into pcscd."],
  },
  {
    key: "agent",
    title: "EMV agent",
    nodeClass: "terminal",
    summary: "A synthetic terminal: the model writes its own commands and reads the answers.",
    ports: [CARD_PORT],
    latencyMs: 0,
    fields: [
      {
        kind: "text",
        key: "task",
        label: "Task",
        placeholder: "Enumerate applications and read every record",
      },
      {
        kind: "select",
        key: "provider",
        label: "Model",
        default: "anthropic",
        options: [
          { value: "anthropic", label: "Anthropic" },
          { value: "openai", label: "OpenAI-compatible" },
          { value: "local", label: "Local (Ollama, vLLM, llama.cpp)" },
        ],
      },
      { kind: "bool", key: "bruteSfi", label: "Brute-force SFI range", default: false },
    ],
    caveats: ["Drives the card on its own. Point it at a card you own."],
  },
  {
    key: "probe",
    title: "Fingerprint probe",
    nodeClass: "terminal",
    summary: "Runs atrium's fingerprint sequence and records what the card answers.",
    ports: [CARD_PORT],
    latencyMs: 0,
    fields: [
      {
        kind: "select",
        key: "depth",
        label: "Depth",
        default: "standard",
        options: [
          { value: "quick", label: "Quick — AID scan and ATR/ATS" },
          { value: "standard", label: "Standard — plus records and DOLs" },
          { value: "deep", label: "Deep — plus SFI sweep" },
        ],
      },
    ],
  },
];

/* ── Card side — nodes that answer ─────────────────────────────────────── */

const CARDS: NodeType[] = [
  {
    key: "pcsc",
    title: "PC/SC reader",
    nodeClass: "card",
    summary: "A contact card in a reader attached to this host.",
    ports: [TERMINAL_PORT],
    device: "pcsc",
    latencyMs: 12,
    fields: [
      { kind: "device", key: "device", label: "Reader", deviceKind: "pcsc" },
    ],
  },
  {
    key: "acr122u.reader",
    title: "ACR122U field",
    nodeClass: "card",
    summary: "A contactless card sitting in an ACR122U's RF field.",
    ports: [TERMINAL_PORT],
    device: "acr122u",
    latencyMs: 9,
    fields: [
      { kind: "device", key: "device", label: "Reader", deviceKind: "acr122u" },
    ],
    caveats: [
      "One ACR122U cannot be a target and an initiator at once. Emulating and reading needs two.",
    ],
  },
  {
    key: "nfcgate",
    title: "NFCGate session",
    nodeClass: "card",
    summary: "A card held in an Android phone's field, reached through an NFCGate server.",
    ports: [TERMINAL_PORT],
    device: "nfcgate",
    latencyMs: 35,
    fields: [
      { kind: "text", key: "host", label: "Server host", default: "127.0.0.1" },
      { kind: "number", key: "port", label: "Server port", default: 5566, min: 1, max: 65535 },
      { kind: "number", key: "session", label: "Session", default: 1, min: 1 },
      {
        kind: "text",
        key: "cafile",
        label: "CA certificate",
        placeholder: "optional, for a TLS server",
        help: "Leave empty for a plaintext server on the local network.",
      },
    ],
    caveats: [
      "Latency here is the phone, the Wi-Fi and the server. It is the biggest single cost in most graphs.",
    ],
  },
  {
    key: "cardproxy",
    title: "Remote card",
    nodeClass: "card",
    summary: "A reader on another machine, reached over atrium's card proxy.",
    ports: [TERMINAL_PORT],
    device: "proxy",
    latencyMs: 20,
    fields: [
      { kind: "text", key: "host", label: "Proxy host", default: "127.0.0.1" },
      { kind: "number", key: "port", label: "Proxy port", default: 7654, min: 1, max: 65535 },
      {
        kind: "text",
        key: "pairing",
        label: "Pairing secret",
        help: "Without this the link is unauthenticated. Keep it on loopback or an SSH tunnel instead.",
      },
    ],
  },
  {
    key: "capture",
    title: "Recorded capture",
    nodeClass: "card",
    summary: "Replays a saved session. No hardware, no card, repeatable.",
    ports: [TERMINAL_PORT],
    device: "file",
    latencyMs: 0,
    fields: [
      {
        kind: "text",
        key: "path",
        label: "Capture file",
        placeholder: "logs/session-2026-09-11.jsonl",
      },
      {
        kind: "bool",
        key: "strict",
        label: "Strict replay",
        default: true,
        help: "Fail on a command the capture does not contain, rather than improvising a response.",
      },
    ],
  },
];

/* ── Transforms — nodes that sit in the middle ─────────────────────────── */

const TRANSFORMS: NodeType[] = [
  {
    key: "mutation",
    title: "Mutation",
    nodeClass: "transform",
    summary: "Rewrites PDOL, AFL, DOL and response tags as the conversation passes.",
    ports: PASSTHROUGH_PORTS,
    latencyMs: 1,
    fields: [
      { kind: "yaml", key: "playbook", label: "Playbook", default: DEFAULT_PLAYBOOK, rows: 18 },
      { kind: "bool", key: "logMutations", label: "Log every rewrite", default: true },
    ],
  },
  {
    key: "inject",
    title: "Injection",
    nodeClass: "transform",
    summary: "Fires extra commands at the card around a trigger instruction.",
    ports: [
      TERMINAL_PORT,
      CARD_PORT,
      {
        id: "oob",
        channel: "apdu",
        role: "requester",
        label: "out of band",
        oob: true,
      },
    ],
    latencyMs: 1,
    fields: [
      { kind: "hex", key: "triggerIns", label: "Trigger INS", default: "AE", placeholder: "AE" },
      {
        kind: "select",
        key: "when",
        label: "When",
        default: "after_response",
        options: [
          { value: "before_command", label: "Before the command" },
          { value: "after_response", label: "After the response" },
        ],
      },
      { kind: "hex", key: "apdu", label: "APDU to inject", placeholder: "80CA9F3600" },
      { kind: "bool", key: "repeat", label: "Fire every time", default: false },
    ],
    caveats: [
      "The injected command reaches the card out of band, so it spends the round trip twice. Free on contact, and enough to time a contactless tap out.",
    ],
  },
  {
    key: "trace",
    title: "Trace tap",
    nodeClass: "transform",
    summary: "Decodes TLV and records the exchange. Changes nothing.",
    ports: PASSTHROUGH_PORTS,
    latencyMs: 0,
    fields: [
      {
        kind: "select",
        key: "detail",
        label: "Detail",
        default: "tlv",
        options: [
          { value: "raw", label: "Raw bytes only" },
          { value: "tlv", label: "Decoded TLV" },
          { value: "annotated", label: "Decoded and annotated" },
        ],
      },
      { kind: "text", key: "path", label: "Write to", placeholder: "logs/trace.jsonl" },
    ],
  },
  {
    key: "swtx",
    title: "Timing",
    nodeClass: "transform",
    summary: "Asks the terminal for more time with S(WTX) when the card is slow.",
    ports: PASSTHROUGH_PORTS,
    latencyMs: 0,
    fields: [
      { kind: "number", key: "wtxm", label: "WTX multiplier", default: 4, min: 1, max: 59 },
      {
        kind: "number",
        key: "thresholdMs",
        label: "Request above (ms)",
        default: 200,
        min: 0,
      },
    ],
    caveats: [
      "Buys time on contact. A contactless kernel may refuse it and drop the transaction.",
    ],
  },
  {
    key: "breakpoint",
    title: "Breakpoint",
    nodeClass: "transform",
    summary: "Holds each matching APDU until you release it from the console.",
    ports: PASSTHROUGH_PORTS,
    latencyMs: 0,
    fields: [
      {
        kind: "hex",
        key: "matchIns",
        label: "Break on INS",
        placeholder: "empty for every command",
      },
      {
        kind: "bool",
        key: "editable",
        label: "Allow editing the held bytes",
        default: true,
      },
    ],
    caveats: [
      "Stops the clock dead. Any real terminal will time out while you think.",
    ],
  },
];

export const NODE_TYPES: NodeType[] = [...TERMINALS, ...TRANSFORMS, ...CARDS];

const BY_KEY = new Map(NODE_TYPES.map((t) => [t.key, t]));

export function nodeType(key: string): NodeType | undefined {
  return BY_KEY.get(key);
}

/** Throws rather than returning undefined — a graph naming an unknown type is
 * corrupt, and silently rendering an empty box hides that. */
export function requireNodeType(key: string): NodeType {
  const t = BY_KEY.get(key);
  if (!t) throw new Error(`Unknown node type: ${key}`);
  return t;
}

export const CLASS_LABEL: Record<string, string> = {
  terminal: "Terminal side",
  transform: "Transforms",
  card: "Card side",
  host: "Host layer",
};

export const CLASS_ORDER = ["terminal", "transform", "card", "host"] as const;

/** Config object with every field at its declared default. */
export function defaultConfig(type: NodeType): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of type.fields) {
    if ("default" in f && f.default !== undefined) out[f.key] = f.default;
  }
  return out;
}

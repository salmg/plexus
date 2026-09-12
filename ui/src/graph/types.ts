/**
 * The Plexus graph model.
 *
 * These types are the contract between the editor, the saved `.plexus.yaml`
 * file and the Python runtime.  `plexus/graph.py` mirrors them; change one and
 * change the other, or a graph that draws will not run.
 *
 * The whole design rests on one idea.  An edge in Plexus is not a push of data
 * downstream — it is a *conversation*.  A command APDU travels from the
 * requester to the responder and a response comes back along the same edge.
 * So ports are not "in" and "out", they are:
 *
 *   requester  — I ask.  I need something on the other end that answers.
 *   responder  — I answer.  I present myself as the thing being asked.
 *
 * On the `apdu` channel those read as "card port" (a requester: it wants a card
 * to talk to) and "terminal port" (a responder: it behaves as a card would).
 * An edge is legal when the two ports share a channel and hold opposite roles,
 * and it is always drawn requester → responder, which is the direction a
 * command travels.
 */

export type Channel = "apdu" | "iso8583";

export type PortRole = "requester" | "responder";

export interface PortSpec {
  /** Unique within its node type. */
  id: string;
  channel: Channel;
  role: PortRole;
  label: string;
  /**
   * Out-of-band ports carry traffic that does not belong to the main
   * conversation — command injection reaching past the terminal straight to
   * the card, for one.  Drawn dashed and beneath the node, because the honest
   * thing is to show that the round trip is being spent twice.
   */
  oob?: boolean;
}

/**
 * Where a node sits in a chain.  `terminal` nodes only ask, `card` nodes only
 * answer, `transform` nodes do both and therefore go in the middle.
 */
export type NodeClass = "terminal" | "transform" | "card" | "host";

export interface NodePosition {
  x: number;
  y: number;
}

export interface PlexusNode {
  id: string;
  /** Catalogue key, e.g. `acr122u.target`. */
  type: string;
  /** User-supplied name.  Falls back to the catalogue title when empty. */
  label?: string;
  position: NodePosition;
  config: Record<string, unknown>;
  /** Which machine runs this node.  Nodes in different hosts imply a link. */
  host?: string;
  /** Kept in the graph but skipped at run time. */
  disabled?: boolean;
}

export interface PortRef {
  node: string;
  port: string;
}

export interface PlexusEdge {
  id: string;
  /** Always the requester side. */
  from: PortRef;
  /** Always the responder side. */
  to: PortRef;
}

export interface HostSpec {
  id: string;
  name: string;
  /** Empty means "wherever the runtime is", i.e. no network link. */
  address?: string;
  note?: string;
}

export interface PlexusGraph {
  version: 1;
  name: string;
  hosts: HostSpec[];
  nodes: PlexusNode[];
  edges: PlexusEdge[];
}

export function emptyGraph(name = "untitled"): PlexusGraph {
  return { version: 1, name, hosts: [], nodes: [], edges: [] };
}

/* ── Devices ──────────────────────────────────────────────────────────────
 * A node does not contain a reader, it *binds* one.  Two nodes binding the
 * same physical device is the single most common way to build a graph that
 * looks right and times out — so the binding is modelled explicitly and the
 * validator refuses the second claim.
 */

export type DeviceKind =
  | "pcsc"
  | "acr122u"
  | "simtrace2"
  | "nfcgate"
  | "proxy"
  | "file";

export interface Device {
  id: string;
  kind: DeviceKind;
  name: string;
  /** Host id this device is physically attached to. */
  host: string;
  available: boolean;
  detail?: string;
}

/* ── Run-time state ─────────────────────────────────────────────────────── */

export type RunState =
  | "stopped"
  | "arming"
  | "running"
  | "stopping"
  | "faulted";

export type NodeStatus = "idle" | "ready" | "live" | "error";

export interface NodeTelemetry {
  status: NodeStatus;
  apdus: number;
  errors: number;
  /** Milliseconds this node added to the last round trip. */
  lastMs: number | null;
  meanMs: number | null;
  detail?: string;
}

export interface EdgeTelemetry {
  apdus: number;
  lastMs: number | null;
  bytesOut: number;
  bytesIn: number;
}

export type ApduDirection = "command" | "response";

export interface ApduEvent {
  seq: number;
  /** Milliseconds since the run started. */
  t: number;
  edge: string;
  dir: ApduDirection;
  /** Uppercase hex, no separators. */
  hex: string;
  /** Round-trip time, present on responses only. */
  ms?: number;
  /** Set when a transform rewrote these bytes. */
  mutatedBy?: string;
  note?: string;
}

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogLine {
  t: number;
  level: LogLevel;
  source: string;
  text: string;
}

/**
 * Graph validation.
 *
 * Two jobs, and they run at different moments.  `canConnect` runs while an edge
 * is being dragged, so a wire that would not work never lands.  `validate` runs
 * over the whole graph and produces the list you see before arming a run.
 *
 * Most of what is checked here exists in atrium already, as prose in an
 * exception message — `transport/source.py` spends a paragraph explaining that
 * the reader presenting the emulated card cannot also hold the card being
 * relayed.  That is a rule about a graph, and a graph is the right place to
 * enforce it: not an error at run time, but an edge you cannot draw.
 */

import { nodeType } from "../catalogue";
import type { PortSpec } from "./types";
import type { Device, PlexusEdge, PlexusGraph, PlexusNode, PortRef } from "./types";

export type ProblemLevel = "error" | "warning";

export interface Problem {
  code: string;
  level: ProblemLevel;
  message: string;
  nodeId?: string;
  edgeId?: string;
}

/**
 * Rules of thumb, not figures from a specification.  Contactless kernels
 * enforce a per-exchange timing budget; the exact number varies by kernel and
 * is not something this tool can know.  These exist so a graph that is
 * obviously too slow says so before hardware is plugged in.
 *
 * Compared against the *round trip* — a command out and a response back —
 * because that is the interval a terminal actually times.  The first version
 * compared the one-way sum against 120 ms, which no graph anyone could draw
 * ever reached: the dearest chain the catalogue allows is 43 ms one way, so the
 * check could not fire and was worse than no check at all.
 *
 * Known simplification: an injection node is counted at its own small cost, not
 * at the extra round trip it really spends reaching the card out of band.
 *
 * Keep these in step with `plexus/graph.py`.
 */
export const TAP_BUDGET_WARN_MS = 60;
export const TAP_BUDGET_FAIL_MS = 150;

const CONTACTLESS_TERMINALS = new Set(["acr122u.target"]);

export function portsOf(node: PlexusNode): PortSpec[] {
  return nodeType(node.type)?.ports ?? [];
}

export function findPort(node: PlexusNode, portId: string): PortSpec | undefined {
  return portsOf(node).find((p) => p.id === portId);
}

function nodeLabel(node: PlexusNode): string {
  return node.label?.trim() || nodeType(node.type)?.title || node.type;
}

function edgeTouches(edge: PlexusEdge, ref: PortRef): boolean {
  return (
    (edge.from.node === ref.node && edge.from.port === ref.port) ||
    (edge.to.node === ref.node && edge.to.port === ref.port)
  );
}

export interface ConnectVerdict {
  ok: boolean;
  reason?: string;
}

/**
 * May these two ports be wired together?
 *
 * Called on every pointer move during a drag, so it stays cheap and says why
 * in one sentence — the reason is shown to the user, not logged.
 */
export function canConnect(
  graph: PlexusGraph,
  from: PortRef,
  to: PortRef,
): ConnectVerdict {
  if (from.node === to.node) {
    return { ok: false, reason: "A node cannot talk to itself." };
  }

  const src = graph.nodes.find((n) => n.id === from.node);
  const dst = graph.nodes.find((n) => n.id === to.node);
  if (!src || !dst) return { ok: false, reason: "Node not in this graph." };

  const srcPort = findPort(src, from.port);
  const dstPort = findPort(dst, to.port);
  if (!srcPort || !dstPort) return { ok: false, reason: "Unknown port." };

  if (srcPort.channel !== dstPort.channel) {
    return {
      ok: false,
      reason: `A ${srcPort.channel} port does not carry ${dstPort.channel}.`,
    };
  }

  if (srcPort.role === dstPort.role) {
    const both = srcPort.role === "requester" ? "ask" : "answer";
    return {
      ok: false,
      reason: `Both ports ${both}. An edge joins one that asks to one that answers.`,
    };
  }

  // Each port carries exactly one conversation.  Fanning a card out to two
  // terminals is not a graph Plexus can run — it is two graphs.
  for (const ref of [from, to]) {
    if (graph.edges.some((e) => edgeTouches(e, ref))) {
      const n = ref.node === from.node ? src : dst;
      const p = ref.node === from.node ? srcPort : dstPort;
      return {
        ok: false,
        reason: `The ${p.label} port on ${nodeLabel(n)} is already wired.`,
      };
    }
  }

  if (createsCycle(graph, from.node, to.node)) {
    return { ok: false, reason: "That would close a loop." };
  }

  return { ok: true };
}

/** Would an edge from `fromNode` to `toNode` make the graph cyclic? */
function createsCycle(graph: PlexusGraph, fromNode: string, toNode: string): boolean {
  const seen = new Set<string>();
  const stack = [toNode];
  while (stack.length) {
    const current = stack.pop()!;
    if (current === fromNode) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    for (const e of graph.edges) {
      if (e.from.node === current) stack.push(e.to.node);
    }
  }
  return false;
}

/* ── Whole-graph validation ─────────────────────────────────────────────── */

export function validate(graph: PlexusGraph, devices: Device[]): Problem[] {
  const problems: Problem[] = [];
  const active = graph.nodes.filter((n) => !n.disabled);

  if (active.length === 0) {
    problems.push({
      code: "empty",
      level: "warning",
      message: "The graph is empty. Drag a node from the palette to start.",
    });
    return problems;
  }

  problemsFromPorts(graph, active, problems);
  problemsFromDevices(active, devices, problems);
  problemsFromHosts(graph, active, problems);
  problemsFromTiming(graph, active, problems);

  return problems;
}

function problemsFromPorts(
  graph: PlexusGraph,
  active: PlexusNode[],
  out: Problem[],
): void {
  for (const node of active) {
    const type = nodeType(node.type);
    if (!type) {
      out.push({
        code: "unknown-type",
        level: "error",
        nodeId: node.id,
        message: `Unknown node type "${node.type}". This graph was saved by a different version.`,
      });
      continue;
    }

    for (const port of type.ports) {
      const wired = graph.edges.some((e) =>
        edgeTouches(e, { node: node.id, port: port.id }),
      );
      if (wired) continue;

      // A requester with nothing to ask cannot run.  A responder with nobody
      // asking is merely unused, which is a fine state mid-build.
      if (port.role === "requester" && !port.oob) {
        out.push({
          code: "unwired-requester",
          level: "error",
          nodeId: node.id,
          message: `${nodeLabel(node)} has nothing on its ${port.label} port. Wire it to something that answers.`,
        });
      } else if (port.role === "responder") {
        out.push({
          code: "unwired-responder",
          level: "warning",
          nodeId: node.id,
          message: `Nothing is asking ${nodeLabel(node)} anything. It will sit idle.`,
        });
      }
    }
  }
}

function problemsFromDevices(
  active: PlexusNode[],
  devices: Device[],
  out: Problem[],
): void {
  const claims = new Map<string, PlexusNode[]>();

  for (const node of active) {
    const type = nodeType(node.type);
    if (!type?.device) continue;
    const field = type.fields.find((f) => f.kind === "device");
    if (!field) continue;
    const bound = node.config[field.key];
    if (typeof bound !== "string" || bound === "") {
      out.push({
        code: "unbound-device",
        level: "error",
        nodeId: node.id,
        message: `${nodeLabel(node)} has no device selected.`,
      });
      continue;
    }
    claims.set(bound, [...(claims.get(bound) ?? []), node]);
  }

  for (const [deviceId, holders] of claims) {
    const device = devices.find((d) => d.id === deviceId);

    if (device && !device.available) {
      out.push({
        code: "device-missing",
        level: "error",
        nodeId: holders[0].id,
        message: `${device.name} is not present. Plug it in, or pick another.`,
      });
    }

    if (holders.length > 1) {
      const names = holders.map(nodeLabel).join(" and ");
      out.push({
        code: "device-contended",
        level: "error",
        nodeId: holders[1].id,
        message: `${names} both claim ${device?.name ?? deviceId}. One device cannot be a target and an initiator at the same time — the failure looks like a hardware fault, not a message.`,
      });
    }
  }
}

function problemsFromHosts(
  graph: PlexusGraph,
  active: PlexusNode[],
  out: Problem[],
): void {
  const hostOf = new Map(active.map((n) => [n.id, n.host ?? ""]));

  for (const edge of graph.edges) {
    const a = hostOf.get(edge.from.node);
    const b = hostOf.get(edge.to.node);
    if (a === undefined || b === undefined || a === b) continue;

    // The edge leaves the machine.  Whether it is protected depends on the
    // node at the far end, so name the node rather than the edge.
    const target = active.find((n) => n.id === edge.to.node);
    if (!target || !nodeType(target.type)) continue;

    if (!isLinkProtected(target)) {
      out.push({
        code: "unpaired-link",
        level: "warning",
        edgeId: edge.id,
        nodeId: target.id,
        message: `This edge crosses machines and carries card traffic unauthenticated. Set a pairing secret on ${nodeLabel(target)}, or tunnel it over SSH.`,
      });
    }
  }
}

/**
 * Is the far end of a cross-machine edge authenticated at all?
 *
 * Either a pairing secret (the card proxy) or a CA file (an NFCGate server
 * speaking TLS) counts.  Neither is checked for validity here — that is the
 * runtime's job, and a wrong secret fails loudly where a missing one fails
 * silently, which is the case worth warning about.
 */
function isLinkProtected(node: PlexusNode): boolean {
  const nonEmpty = (v: unknown) => typeof v === "string" && v.trim() !== "";
  return nonEmpty(node.config.pairing) || nonEmpty(node.config.cafile);
}

/** Longest requester→responder walk, in milliseconds of expected latency. */
export function chainLatency(graph: PlexusGraph, startId: string): number {
  let total = 0;
  let current: string | undefined = startId;
  const seen = new Set<string>();

  while (current && !seen.has(current)) {
    seen.add(current);
    const node = graph.nodes.find((n) => n.id === current);
    if (!node) break;
    total += nodeType(node.type)?.latencyMs ?? 0;
    const next: PlexusEdge | undefined = graph.edges.find(
      (e) => e.from.node === current && e.from.port !== "oob",
    );
    current = next?.to.node;
  }

  return total;
}

function problemsFromTiming(
  graph: PlexusGraph,
  active: PlexusNode[],
  out: Problem[],
): void {
  for (const node of active) {
    if (!CONTACTLESS_TERMINALS.has(node.type)) continue;

    // Out and back: the interval the terminal times, not the one-way sum.
    const ms = chainLatency(graph, node.id) * 2;
    if (ms >= TAP_BUDGET_FAIL_MS) {
      out.push({
        code: "tap-budget",
        level: "error",
        nodeId: node.id,
        message: `About ${ms} ms per exchange, out and back. A contactless kernel will abandon the transaction well before that.`,
      });
    } else if (ms >= TAP_BUDGET_WARN_MS) {
      out.push({
        code: "tap-budget",
        level: "warning",
        nodeId: node.id,
        message: `About ${ms} ms per exchange, out and back. Close to what a contactless kernel will tolerate — expect dropped taps.`,
      });
    }
  }
}

export function hasBlockingProblem(problems: Problem[]): boolean {
  return problems.some((p) => p.level === "error");
}

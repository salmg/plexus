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
import { nodeTitle, portLabel } from "../i18n/catalogue";
import { t } from "../i18n";
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
  const type = nodeType(node.type);
  return node.label?.trim() || (type ? nodeTitle(type) : node.type);
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
    return { ok: false, reason: t("connect.self") };
  }

  const src = graph.nodes.find((n) => n.id === from.node);
  const dst = graph.nodes.find((n) => n.id === to.node);
  if (!src || !dst) return { ok: false, reason: t("connect.unknownNode") };

  const srcPort = findPort(src, from.port);
  const dstPort = findPort(dst, to.port);
  if (!srcPort || !dstPort) return { ok: false, reason: t("connect.unknownPort") };

  if (srcPort.channel !== dstPort.channel) {
    return {
      ok: false,
      reason: t("connect.wrongChannel", {
        from: srcPort.channel,
        to: dstPort.channel,
      }),
    };
  }

  if (srcPort.role === dstPort.role) {
    return {
      ok: false,
      reason: t(
        srcPort.role === "requester" ? "connect.bothAsk" : "connect.bothAnswer",
      ),
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
        reason: t("connect.alreadyWired", {
          port: portLabel(p),
          node: nodeLabel(n),
        }),
      };
    }
  }

  if (createsCycle(graph, from.node, to.node)) {
    return { ok: false, reason: t("connect.loop") };
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
      message: t("check.empty"),
    });
    return problems;
  }

  problemsFromEdges(graph, active, problems);
  problemsFromPorts(graph, active, problems);
  problemsFromDevices(active, devices, problems);
  problemsFromHosts(graph, active, problems);
  problemsFromTiming(graph, active, problems);

  return problems;
}

/**
 * Are the edges legal — for a graph that arrived rather than one being drawn?
 *
 * `canConnect` refuses these while the wire is in the air, which covers every
 * edge *you* make. It covers nothing about a graph that came from a file: Open
 * a `.plexus.yaml` with a cycle, a self-edge, two requesters wired together or
 * one port carrying two conversations and the canvas drew it clean, Run stayed
 * enabled, and the runtime found out.
 *
 * Same conditions and same wording as `canConnect` above and as
 * `_edge_problems` in `plexus/graph.py`, so all three agree about what a legal
 * graph is.
 */
function problemsFromEdges(
  graph: PlexusGraph,
  active: PlexusNode[],
  out: Problem[],
): void {
  const alive = new Set(active.map((n) => n.id));
  const claimed = new Map<string, string>();
  const nodeOf = (id: string) => graph.nodes.find((n) => n.id === id);

  for (const edge of graph.edges) {
    // An edge onto a disabled node is not an error — disabling is how you park
    // part of a rig.
    if (!alive.has(edge.from.node) || !alive.has(edge.to.node)) continue;

    const src = nodeOf(edge.from.node);
    const dst = nodeOf(edge.to.node);
    if (!src || !dst) continue;

    if (edge.from.node === edge.to.node) {
      out.push({
        code: "self-edge",
        level: "error",
        nodeId: src.id,
        edgeId: edge.id,
        message: t("check.selfEdge", { node: nodeLabel(src) }),
      });
      continue;
    }

    const srcPort = findPort(src, edge.from.port);
    const dstPort = findPort(dst, edge.to.port);

    if (!srcPort || !dstPort) {
      const missing = srcPort ? edge.to : edge.from;
      const owner = srcPort ? dst : src;
      out.push({
        code: "unknown-port",
        level: "error",
        nodeId: owner.id,
        edgeId: edge.id,
        message: t("check.unknownPort", {
          port: missing.port,
          node: nodeLabel(owner),
        }),
      });
      continue;
    }

    if (srcPort.channel !== dstPort.channel) {
      out.push({
        code: "wrong-channel",
        level: "error",
        nodeId: src.id,
        edgeId: edge.id,
        message: t("connect.wrongChannel", {
          from: srcPort.channel,
          to: dstPort.channel,
        }),
      });
      continue;
    }

    if (srcPort.role === dstPort.role) {
      out.push({
        code: "same-role",
        level: "error",
        nodeId: src.id,
        edgeId: edge.id,
        message: t(
          srcPort.role === "requester" ? "connect.bothAsk" : "connect.bothAnswer",
        ),
      });
      continue;
    }

    for (const [ref, port, owner] of [
      [edge.from, srcPort, src],
      [edge.to, dstPort, dst],
    ] as const) {
      const key = `${ref.node}:${ref.port}`;
      const held = claimed.get(key);
      if (held !== undefined && held !== edge.id) {
        out.push({
          code: "port-contended",
          level: "error",
          nodeId: owner.id,
          edgeId: edge.id,
          message: t("check.portContended", {
            port: portLabel(port),
            node: nodeLabel(owner),
          }),
        });
      } else {
        claimed.set(key, edge.id);
      }
    }
  }

  const loop = findCycle(graph, alive);
  if (loop.length > 0) {
    out.push({
      code: "cycle",
      level: "error",
      nodeId: loop[0],
      message: t("check.cycle", {
        nodes: loop
          .map((id) => {
            const node = nodeOf(id);
            return node ? nodeLabel(node) : id;
          })
          .join(" → "),
      }),
    });
  }
}

/** The first cycle reachable by following edges, as a node-id path. */
function findCycle(graph: PlexusGraph, alive: Set<string>): string[] {
  const following = new Map<string, string[]>();
  for (const edge of graph.edges) {
    following.set(edge.from.node, [
      ...(following.get(edge.from.node) ?? []),
      edge.to.node,
    ]);
  }

  const GREY = 1;
  const BLACK = 2;
  const shade = new Map<string, number>();

  const walk = (id: string, path: string[]): string[] => {
    shade.set(id, GREY);
    path.push(id);
    for (const next of following.get(id) ?? []) {
      const seen = shade.get(next);
      if (seen === GREY) return [...path.slice(path.indexOf(next)), next];
      if (seen === undefined) {
        const found = walk(next, path);
        if (found.length > 0) return found;
      }
    }
    path.pop();
    shade.set(id, BLACK);
    return [];
  };

  for (const id of alive) {
    if (shade.get(id) === undefined) {
      const found = walk(id, []);
      if (found.length > 0) return found;
    }
  }
  return [];
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
        message: t("check.unknownType", { type: node.type }),
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
          message: t("check.unwiredRequester", {
            node: nodeLabel(node),
            port: portLabel(port),
          }),
        });
      } else if (port.role === "responder") {
        out.push({
          code: "unwired-responder",
          level: "warning",
          nodeId: node.id,
          message: t("check.unwiredResponder", { node: nodeLabel(node) }),
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
        message: t("check.unboundDevice", { node: nodeLabel(node) }),
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
        message: t("check.deviceMissing", { name: device.name }),
      });
    }

    if (holders.length > 1) {
      const names = holders.map(nodeLabel).join(" and ");
      out.push({
        code: "device-contended",
        level: "error",
        nodeId: holders[1].id,
        message: t("check.deviceContended", {
          nodes: names,
          name: device?.name ?? deviceId,
        }),
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
        message: t("check.unpairedLink", { node: nodeLabel(target) }),
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
        message: t("check.tapBudget.fail", { ms }),
      });
    } else if (ms >= TAP_BUDGET_WARN_MS) {
      out.push({
        code: "tap-budget",
        level: "warning",
        nodeId: node.id,
        message: t("check.tapBudget.warn", { ms }),
      });
    }
  }
}

export function hasBlockingProblem(problems: Problem[]): boolean {
  return problems.some((p) => p.level === "error");
}

/**
 * The rules that decide which nodes may be wired together, and where a command
 * actually goes once they are.
 *
 * `canConnect` is the load-bearing one: it runs while the wire is still in the
 * air, so a connection that could not work is a connection you cannot draw.
 * Every refusal here was previously verified only by dragging wires by hand.
 */

import { describe, expect, it } from "vitest";

import { emptyGraph } from "../src/graph/types";
import type { PlexusGraph, PlexusNode } from "../src/graph/types";
import { canConnect, chainOf } from "../src/graph/validate";

const CAPTURE = "tests/captures/mastercard-probe.jsonl";

function node(id: string, type: string, config: Record<string, unknown> = {}): PlexusNode {
  return { id, type, position: { x: 0, y: 0 }, config };
}

function graphOf(nodes: PlexusNode[], edges: PlexusGraph["edges"] = []): PlexusGraph {
  return { ...emptyGraph("test"), nodes, edges };
}

function edge(from: string, fromPort: string, to: string, toPort: string) {
  return {
    id: `${from}:${fromPort}->${to}:${toPort}`,
    from: { node: from, port: fromPort },
    to: { node: to, port: toPort },
  };
}

const probe = () => node("probe-1", "probe", { depth: "quick" });
const capture = (id = "capture-1") => node(id, "capture", { path: CAPTURE });
const breakpointNode = (id = "bp-1") => node(id, "breakpoint");

describe("canConnect", () => {
  it("allows a requester to reach a responder", () => {
    const g = graphOf([probe(), capture()]);
    const verdict = canConnect(
      g,
      { node: "probe-1", port: "card" },
      { node: "capture-1", port: "terminal" },
    );
    expect(verdict.ok).toBe(true);
  });

  it("refuses a node wired to itself", () => {
    const g = graphOf([breakpointNode()]);
    const verdict = canConnect(
      g,
      { node: "bp-1", port: "card" },
      { node: "bp-1", port: "terminal" },
    );
    expect(verdict.ok).toBe(false);
    expect(verdict.reason).toBeTruthy();
  });

  it("refuses two things that both ask", () => {
    const g = graphOf([probe(), node("probe-2", "probe", { depth: "quick" })]);
    const verdict = canConnect(
      g,
      { node: "probe-1", port: "card" },
      { node: "probe-2", port: "card" },
    );
    expect(verdict.ok).toBe(false);
  });

  it("refuses two things that both answer", () => {
    const g = graphOf([capture("capture-1"), capture("capture-2")]);
    const verdict = canConnect(
      g,
      { node: "capture-1", port: "terminal" },
      { node: "capture-2", port: "terminal" },
    );
    expect(verdict.ok).toBe(false);
  });

  it("refuses a second conversation on a port that already has one", () => {
    const g = graphOf(
      [probe(), capture("capture-1"), capture("capture-2")],
      [edge("probe-1", "card", "capture-1", "terminal")],
    );
    const verdict = canConnect(
      g,
      { node: "probe-1", port: "card" },
      { node: "capture-2", port: "terminal" },
    );
    expect(verdict.ok).toBe(false);
  });

  it("refuses an edge that would close a loop", () => {
    const g = graphOf(
      [breakpointNode("bp-1"), breakpointNode("bp-2")],
      [edge("bp-1", "card", "bp-2", "terminal")],
    );
    const verdict = canConnect(
      g,
      { node: "bp-2", port: "card" },
      { node: "bp-1", port: "terminal" },
    );
    expect(verdict.ok).toBe(false);
  });

  it("refuses an unknown port", () => {
    const g = graphOf([probe(), capture()]);
    const verdict = canConnect(
      g,
      { node: "probe-1", port: "antenna" },
      { node: "capture-1", port: "terminal" },
    );
    expect(verdict.ok).toBe(false);
  });
});

describe("chainOf", () => {
  it("walks from the thing that only asks to the thing that only answers", () => {
    const g = graphOf(
      [probe(), breakpointNode(), capture()],
      [
        edge("probe-1", "card", "bp-1", "terminal"),
        edge("bp-1", "card", "capture-1", "terminal"),
      ],
    );
    expect(chainOf(g).map((n) => n.id)).toEqual(["probe-1", "bp-1", "capture-1"]);
  });

  it("is empty when nothing asks", () => {
    const g = graphOf(
      [breakpointNode(), capture()],
      [edge("bp-1", "card", "capture-1", "terminal")],
    );
    expect(chainOf(g)).toEqual([]);
  });

  it("stops at a parked node, because disabling is how you park a rig", () => {
    const parked = { ...capture(), disabled: true };
    const g = graphOf([probe(), parked], [edge("probe-1", "card", "capture-1", "terminal")]);
    expect(chainOf(g).map((n) => n.id)).toEqual(["probe-1"]);
  });

  it("does not follow the out-of-band line as if it were a step", () => {
    // The inject node's dashed edge reaches past the chain straight to the
    // card. Counting it as a hop would double the latency of every rig with an
    // injection in it.
    const g = graphOf(
      [
        probe(),
        node("inject-1", "inject", {
          triggerIns: "AE",
          apdu: "80CA9F1700",
          when: "before_command",
        }),
        capture(),
      ],
      [
        edge("probe-1", "card", "inject-1", "terminal"),
        edge("inject-1", "card", "capture-1", "terminal"),
        edge("inject-1", "oob", "capture-1", "terminal"),
      ],
    );
    expect(chainOf(g).map((n) => n.id)).toEqual(["probe-1", "inject-1", "capture-1"]);
  });
});

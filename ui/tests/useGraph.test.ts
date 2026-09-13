/**
 * The document store: what a copy carries, and what undo puts back.
 *
 * These went untested for as long as there was no runner. The clipboard is the
 * part worth pinning — it is the only code in the editor that mints ids, and an
 * id collision would silently overwrite a node rather than fail.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { emptyGraph } from "../src/graph/types";
import { useGraph } from "../src/store/useGraph";

const CAPTURE = "tests/captures/mastercard-probe.jsonl";

/** A probe asking a capture, wired — the smallest rig that runs. */
function rig() {
  return {
    ...emptyGraph("test"),
    nodes: [
      {
        id: "probe-1",
        type: "probe",
        position: { x: 0, y: 0 },
        config: { depth: "quick" },
      },
      {
        id: "capture-1",
        type: "capture",
        position: { x: 300, y: 0 },
        config: { path: CAPTURE },
      },
    ],
    edges: [
      {
        id: "probe-1:card->capture-1:terminal",
        from: { node: "probe-1", port: "card" },
        to: { node: "capture-1", port: "terminal" },
      },
    ],
  };
}

const store = () => useGraph.getState();

beforeEach(() => {
  useGraph.setState({
    graph: rig(),
    selection: [],
    past: [],
    future: [],
    clipboard: null,
    dirty: false,
  });
});

describe("duplicate", () => {
  it("clones a node with a fresh id and keeps its config", () => {
    const made = store().duplicate(["capture-1"]);
    expect(made).toHaveLength(1);
    expect(made[0]).not.toBe("capture-1");

    const clone = store().graph.nodes.find((n) => n.id === made[0])!;
    expect(clone.type).toBe("capture");
    expect(clone.config.path).toBe(CAPTURE);
    expect(store().selection).toEqual(made);
  });

  it("does not share a config object with the original", () => {
    const [id] = store().duplicate(["capture-1"]);
    store().updateConfig(id, "path", "somewhere/else.jsonl");

    const original = store().graph.nodes.find((n) => n.id === "capture-1")!;
    expect(original.config.path).toBe(CAPTURE);
  });

  it("mints distinct ids when cloning several of one type at once", () => {
    store().duplicate(["capture-1"]);
    const all = store().graph.nodes.filter((n) => n.type === "capture");
    const made = store().duplicate(all.map((n) => n.id));

    expect(new Set(made).size).toBe(made.length);
    const ids = store().graph.nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("carries the wiring between the nodes it clones", () => {
    const made = store().duplicate(["probe-1", "capture-1"]);
    const cloned = new Set(made);
    const between = store().graph.edges.filter(
      (e) => cloned.has(e.from.node) && cloned.has(e.to.node),
    );
    expect(between).toHaveLength(1);
  });

  it("leaves an edge behind when only one of its ends is cloned", () => {
    const before = store().graph.edges.length;
    store().duplicate(["capture-1"]);
    // The clone is unwired: an edge with one end outside the copy is not part
    // of what was copied.
    expect(store().graph.edges).toHaveLength(before);
  });
});

describe("copy and paste", () => {
  it("pastes nothing when nothing was copied", () => {
    expect(store().paste()).toEqual([]);
    expect(store().graph.nodes).toHaveLength(2);
  });

  it("round-trips a wired pair", () => {
    store().copy(["probe-1", "capture-1"]);
    expect(store().clipboard!.nodes).toHaveLength(2);
    expect(store().clipboard!.edges).toHaveLength(1);

    const made = store().paste();
    expect(made).toHaveLength(2);
    expect(store().graph.nodes).toHaveLength(4);

    const cloned = new Set(made);
    expect(
      store().graph.edges.filter(
        (e) => cloned.has(e.from.node) && cloned.has(e.to.node),
      ),
    ).toHaveLength(1);
  });

  it("pastes at the point it is given", () => {
    store().copy(["capture-1"]);
    const [id] = store().paste({ x: 900, y: 500 });
    const pasted = store().graph.nodes.find((n) => n.id === id)!;
    expect(pasted.position).toEqual({ x: 900, y: 500 });
  });

  it("survives the original being changed after the copy", () => {
    store().copy(["capture-1"]);
    store().updateConfig("capture-1", "path", "changed.jsonl");

    const [id] = store().paste();
    const pasted = store().graph.nodes.find((n) => n.id === id)!;
    expect(pasted.config.path).toBe(CAPTURE);
  });

  it("can be pasted twice without colliding", () => {
    store().copy(["capture-1"]);
    const first = store().paste();
    const second = store().paste();
    expect(first).not.toEqual(second);
    const ids = store().graph.nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("undo", () => {
  it("takes back a whole paste in one step", () => {
    store().copy(["probe-1", "capture-1"]);
    store().paste();
    expect(store().graph.nodes).toHaveLength(4);

    store().undo();
    expect(store().graph.nodes).toHaveLength(2);
    expect(store().graph.edges).toHaveLength(1);
  });

  it("takes back a duplicate in one step", () => {
    store().duplicate(["probe-1", "capture-1"]);
    expect(store().graph.nodes).toHaveLength(4);
    store().undo();
    expect(store().graph.nodes).toHaveLength(2);
  });

  it("does not treat the clipboard as an edit", () => {
    store().copy(["capture-1"]);
    expect(store().past).toHaveLength(0);
  });
});

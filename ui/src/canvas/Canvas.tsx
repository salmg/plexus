/**
 * The canvas.
 *
 * React Flow owns pan, zoom, selection and the wire being dragged; the store
 * owns the graph.  Everything React Flow reports is translated back into a
 * store action rather than kept in React Flow's own state, so the saved file
 * and what you see can never drift.
 *
 * When a connection is refused the reason is shown, not swallowed.  The whole
 * argument for a typed port model is that a mistake becomes an edge you cannot
 * draw — which only helps if the tool says why.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import type { Connection, Edge, Node, NodeChange } from "@xyflow/react";

import { nodeType } from "../catalogue";
import { canConnect } from "../graph/validate";
import type { Problem } from "../graph/validate";
import { useGraph } from "../store/useGraph";
import { useRuntime } from "../store/useRuntime";
import { NODE_HEIGHT, NODE_WIDTH, PlexusNodeView } from "./PlexusNode";
import type { PlexusNodeData } from "./PlexusNode";

export const PALETTE_DRAG_TYPE = "application/x-plexus-node";

const nodeTypes = { plexus: PlexusNodeView };

interface CanvasProps {
  problems: Problem[];
}

export function Canvas({ problems }: CanvasProps) {
  const graph = useGraph((s) => s.graph);
  const selection = useGraph((s) => s.selection);
  const select = useGraph((s) => s.select);
  const addNode = useGraph((s) => s.addNode);
  const moveNode = useGraph((s) => s.moveNode);
  const removeNode = useGraph((s) => s.removeNode);
  const connect = useGraph((s) => s.connect);
  const disconnect = useGraph((s) => s.disconnect);
  const edgeTelemetry = useRuntime((s) => s.edges);
  const running = useRuntime((s) => s.state === "running");

  const { screenToFlowPosition, fitView } = useReactFlow();
  const [refusal, setRefusal] = useState<string | null>(null);
  const refusalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapper = useRef<HTMLDivElement | null>(null);
  const fitted = useRef(false);

  /**
   * Fit the graph once, when there is something to fit it into.
   *
   * React Flow's own `fitView` prop runs on mount, before the layout has given
   * the canvas a height — it sees a zero-sized box, clamps to the minimum zoom
   * and leaves the graph as specks in a corner.  Calling it ourselves has the
   * mirror problem: on the first effect pass React Flow has not yet adopted the
   * nodes, so it returns false and does nothing.  Neither condition announces
   * itself, so retry across a few frames and treat the returned boolean — not
   * the call — as the thing that succeeded.
   */
  useEffect(() => {
    if (fitted.current || graph.nodes.length === 0) return;
    const element = wrapper.current;
    if (!element) return;

    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    // Timers, not requestAnimationFrame: a hidden tab stops animation frames
    // entirely, and a graph opened in a background tab would then never be
    // fitted at all — it would sit at 1:1 with half the chain off-screen.
    const attempt = () => {
      if (cancelled || fitted.current) return;
      if (attempts++ > 40) return;

      const ready =
        !document.hidden && element.clientWidth >= 50 && element.clientHeight >= 50;

      if (ready) {
        void fitView({ padding: 0.22, maxZoom: 1, duration: 0 }).then((ok) => {
          if (!cancelled && ok) fitted.current = true;
        });
      }
      timer = setTimeout(attempt, 120);
    };

    const restart = () => {
      if (document.hidden || fitted.current) return;
      attempts = 0;
      clearTimeout(timer);
      attempt();
    };

    attempt();
    document.addEventListener("visibilitychange", restart);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", restart);
    };
  }, [fitView, graph.nodes.length]);

  const problemByNode = useMemo(() => {
    const map = new Map<string, Problem>();
    for (const p of problems) {
      if (!p.nodeId) continue;
      const held = map.get(p.nodeId);
      if (!held || (held.level === "warning" && p.level === "error")) {
        map.set(p.nodeId, p);
      }
    }
    return map;
  }, [problems]);

  const rfNodes = useMemo<Node<PlexusNodeData>[]>(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: "plexus",
        position: n.position,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        selected: n.id === selection,
        data: { nodeId: n.id },
        className: problemByNode.get(n.id)?.level === "error" ? "has-error" : undefined,
      })),
    [graph.nodes, selection, problemByNode],
  );

  const rfEdges = useMemo<Edge[]>(
    () =>
      graph.edges.map((e) => {
        const telemetry = edgeTelemetry[e.id];
        const oob = e.from.port === "oob";
        return {
          id: e.id,
          source: e.from.node,
          sourceHandle: e.from.port,
          target: e.to.node,
          targetHandle: e.to.port,
          animated: running && !!telemetry,
          className: oob ? "pedge pedge--oob" : "pedge",
          label: telemetry ? `${telemetry.apdus.toLocaleString()} apdu` : undefined,
          labelBgPadding: [5, 2] as [number, number],
          labelBgBorderRadius: 3,
        };
      }),
    [graph.edges, edgeTelemetry, running],
  );

  const refuse = useCallback((reason: string) => {
    setRefusal(reason);
    if (refusalTimer.current) clearTimeout(refusalTimer.current);
    refusalTimer.current = setTimeout(() => setRefusal(null), 3200);
  }, []);

  const isValidConnection = useCallback(
    (candidate: Connection | Edge) => {
      if (!candidate.sourceHandle || !candidate.targetHandle) return false;
      return canConnect(
        graph,
        { node: candidate.source, port: candidate.sourceHandle },
        { node: candidate.target, port: candidate.targetHandle },
      ).ok;
    },
    [graph],
  );

  const onConnect = useCallback(
    (candidate: Connection) => {
      if (!candidate.sourceHandle || !candidate.targetHandle) return;
      const from = { node: candidate.source, port: candidate.sourceHandle };
      const to = { node: candidate.target, port: candidate.targetHandle };
      const verdict = canConnect(graph, from, to);
      if (!verdict.ok) {
        refuse(verdict.reason ?? "That connection is not allowed.");
        return;
      }
      connect(from, to);
    },
    [graph, connect, refuse],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<Node<PlexusNodeData>>[]) => {
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          moveNode(change.id, change.position, change.dragging === false);
        } else if (change.type === "remove") {
          removeNode(change.id);
        } else if (change.type === "select" && change.selected) {
          select(change.id);
        }
      }
    },
    [moveNode, removeNode, select],
  );

  const onEdgesDelete = useCallback(
    (edges: Edge[]) => edges.forEach((e) => disconnect(e.id)),
    [disconnect],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const key = event.dataTransfer.getData(PALETTE_DRAG_TYPE);
      if (!key || !nodeType(key)) return;
      const at = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      // Drop where the pointer is, not where the node's corner would land.
      addNode(key, { x: Math.round(at.x - 90), y: Math.round(at.y - 40) });
    },
    [addNode, screenToFlowPosition],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  return (
    <div className="canvas" ref={wrapper} onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesDelete={onEdgesDelete}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onInit={(instance) => {
          // Handy from the browser console while developing; not shipped.
          if (import.meta.env.DEV) {
            (window as unknown as Record<string, unknown>).__rf = instance;
          }
        }}
        onPaneClick={() => select(null)}
        proOptions={{ hideAttribution: false }}
        defaultEdgeOptions={{ type: "default" }}
        minZoom={0.25}
        maxZoom={2}
        deleteKeyCode={["Backspace", "Delete"]}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={18}
          size={1}
          color="var(--canvas-grid-major)"
        />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable ariaLabel="Graph overview" />
      </ReactFlow>

      {graph.nodes.length === 0 && (
        <div className="canvas__hint">
          <p>Drag a node from the palette.</p>
          <p className="canvas__hint-sub">
            A chain runs left to right: something that asks, any number of
            transforms, then something that answers.
          </p>
        </div>
      )}

      {refusal && (
        <div className="canvas__refusal" role="status">
          {refusal}
        </div>
      )}
    </div>
  );
}

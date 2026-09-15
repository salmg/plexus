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
  Panel,
  MarkerType,
  ReactFlow,
  SelectionMode,
  useReactFlow,
} from "@xyflow/react";
import type { Connection, Edge, EdgeChange, Node, NodeChange } from "@xyflow/react";

import { CLASS_ORDER, NODE_TYPES, nodeType } from "../catalogue";
import { Icon } from "../catalogue/icons";
import { useT, useTn } from "../i18n";
import { classLabel, nodeTitle } from "../i18n/catalogue";
import { canConnect } from "../graph/validate";
import type { Problem } from "../graph/validate";
import { useGraph } from "../store/useGraph";
import { useLayout } from "../store/useLayout";
import { remember, remembered } from "../store/remember";
import { useRuntime } from "../store/useRuntime";
import { ContextMenu } from "./ContextMenu";
import type { MenuItem, MenuState } from "./ContextMenu";
import { PlexusEdgeView } from "./PlexusEdge";
import { usePlacement } from "./placement";
import type { DropTarget } from "./placement";
import { NODE_HEIGHT, NODE_WIDTH, PlexusNodeView } from "./PlexusNode";
import type { PlexusNodeData } from "./PlexusNode";

export const PALETTE_DRAG_TYPE = "application/x-plexus-node";

/** Where the overview's open/mini/hidden state is remembered. */
const MINIMAP_KEY = "plexus.minimap";

const nodeTypes = { plexus: PlexusNodeView };
const edgeTypes = { plexus: PlexusEdgeView };

/**
 * A minimap block's class, so CSS can colour it.
 *
 * Deliberately not React Flow's `nodeColor`, which takes a function and
 * memoises the minimap around it: the colours come from CSS custom properties
 * that swap with the theme, and a prop read once at render time keeps whatever
 * it was first given — switch to light and the blocks stay in the dark
 * palette. A class name lets the cascade do it, which needs no re-render and
 * cannot go stale.
 */
function minimapClass(node: Node<PlexusNodeData>): string {
  const data = node.data as PlexusNodeData | undefined;
  const key = data?.nodeId
    ? useGraph.getState().graph.nodes.find((n) => n.id === data.nodeId)?.type
    : undefined;
  const nodeClass = key ? nodeType(key)?.nodeClass : undefined;
  return nodeClass ? `mmnode mmnode--${nodeClass}` : "mmnode";
}

interface CanvasProps {
  problems: Problem[];
}

export function Canvas({ problems }: CanvasProps) {
  const t = useT();
  const tn = useTn();
  const graph = useGraph((s) => s.graph);
  const selection = useGraph((s) => s.selection);
  const select = useGraph((s) => s.select);
  const applySelection = useGraph((s) => s.applySelection);
  const addNode = useGraph((s) => s.addNode);
  const moveNode = useGraph((s) => s.moveNode);
  const removeNodes = useGraph((s) => s.removeNodes);
  const connect = useGraph((s) => s.connect);
  const disconnect = useGraph((s) => s.disconnect);
  const duplicate = useGraph((s) => s.duplicate);
  const copy = useGraph((s) => s.copy);
  const paste = useGraph((s) => s.paste);
  const toggleDisabled = useGraph((s) => s.toggleDisabled);
  const selectAll = useGraph((s) => s.selectAll);
  const clipboard = useGraph((s) => s.clipboard);
  const edgeTelemetry = useRuntime((s) => s.edges);
  const running = useRuntime((s) => s.state === "running");

  const { screenToFlowPosition, fitView } = useReactFlow();
  const [refusal, setRefusal] = useState<string | null>(null);
  const refusalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapper = useRef<HTMLDivElement | null>(null);
  const fitted = useRef(false);

  /**
   * Which tool the left mouse button holds.
   *
   * React Flow's stock arrangement asks for Shift to draw a selection box,
   * which is fine until you are rearranging a rig with one hand.  Select mode
   * puts the box on a plain drag and moves panning to the middle and right
   * buttons, so a group can be boxed, dragged and deleted without touching the
   * keyboard.  Pan mode is the default because that is what a canvas usually
   * does when you grab it.
   */
  const [mode, setMode] = useState<"pan" | "select">("pan");

  /**
   * The overview's three states, remembered across reloads.
   *
   * `open` is the full minimap; `mini` collapses it to a labelled pill that
   * still shows and hides it; `hidden` leaves only a small button to bring it
   * back. Minimize keeps the control on screen, hide clears the corner — the
   * window-manager distinction, because the overview is worth reaching for
   * again more often than it is worth banishing.
   */
  const [minimap, setMinimapState] = useState<"open" | "mini" | "hidden">(() => {
    const saved = remembered(MINIMAP_KEY);
    return saved === "mini" || saved === "hidden" ? saved : "open";
  });
  const setMinimap = useCallback((state: "open" | "mini" | "hidden") => {
    remember(MINIMAP_KEY, state);
    setMinimapState(state);
  }, []);

  /**
   * Whether the pointer is over the overview, which is what reveals its
   * minimize and hide buttons — they are clutter over the graph the rest of
   * the time. A short grace period on leave carries the pointer across the gap
   * between the minimap and the bar above it without the bar flickering out.
   */
  const [mapHover, setMapHover] = useState(false);
  const hoverTimer = useRef<number | null>(null);
  const showBar = useCallback(() => {
    if (hoverTimer.current !== null) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
    setMapHover(true);
  }, []);
  const hideBar = useCallback(() => {
    hoverTimer.current = window.setTimeout(() => setMapHover(false), 150);
  }, []);

  // Native listeners on the minimap and its bar, not React's onMouseEnter:
  // React Flow's MiniMap reads only the props it names and drops the rest, so a
  // handler passed to it never runs. mouseenter/mouseleave do not bubble, so
  // each element is bound directly; the leave timer carries the pointer across
  // the gap between the two without the bar flickering out.
  useEffect(() => {
    if (minimap !== "open") return;
    const nodes = [
      document.querySelector(".react-flow__minimap"),
      document.querySelector(".minimap-bar--float"),
    ].filter((el): el is Element => el !== null);
    for (const el of nodes) {
      el.addEventListener("mouseenter", showBar);
      el.addEventListener("mouseleave", hideBar);
    }
    return () => {
      for (const el of nodes) {
        el.removeEventListener("mouseenter", showBar);
        el.removeEventListener("mouseleave", hideBar);
      }
      if (hoverTimer.current !== null) clearTimeout(hoverTimer.current);
    };
  }, [minimap, showBar, hideBar]);

  /**
   * Which edges are selected.
   *
   * Kept here because the edges handed to React Flow are derived from the
   * store, which has no notion of a selected edge — and React Flow reads
   * selection off the array it is given. Without this the `selected` flag
   * was never set, so Delete had no edge to act on.
   */
  const [selectedEdges, setSelectedEdges] = useState<Set<string>>(new Set());

  /** The open menu — a right-click, a long-press, or a node's `+`. */
  const [menu, setMenu] = useState<MenuState | null>(null);

  /**
   * A finger, rather than a mouse.
   *
   * It decides two things the canvas cannot get right otherwise. Select mode
   * pans with `[1, 2]` — the middle and right mouse buttons — which a touch
   * device does not have, so a finger on the canvas would do nothing at all.
   * And rubber-band selection needs a second pointer to pan with, which it
   * also does not have.
   *
   * Read once: a pointer does not change type under a running session, and a
   * listener here would re-render the canvas for nothing.
   */
  const coarse = useMemo(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(pointer: coarse)").matches,
    [],
  );

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
        selected: selection.includes(n.id),
        // Which ports have nothing on them, so the node can offer a `+` on
        // exactly those. Computed here because this is where the edges are.
        data: {
          nodeId: n.id,
          freePorts: (nodeType(n.type)?.ports ?? [])
            .filter(
              (port) =>
                !graph.edges.some(
                  (e) =>
                    (e.from.node === n.id && e.from.port === port.id) ||
                    (e.to.node === n.id && e.to.port === port.id),
                ),
            )
            .map((port) => port.id),
        },
        className: problemByNode.get(n.id)?.level === "error" ? "has-error" : undefined,
      })),
    [graph.nodes, graph.edges, selection, problemByNode],
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
          selected: selectedEdges.has(e.id),
          // Which way a *command* travels. The response comes back along the
          // same wire, so one arrow is half the story — but the half that was
          // missing, and left-to-right was something you had to assume.
          //
          // A CSS variable rather than a colour: React Flow puts this in an
          // inline style on the marker, so `var()` resolves and the arrowhead
          // follows the theme and the wire's own state.
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 16,
            height: 16,
            color: oob
              ? "var(--edge-oob)"
              : running && telemetry
                ? "var(--edge-live)"
                : "var(--edge)",
          },
          className: oob ? "pedge pedge--oob" : "pedge",
          // The count rides in `data` rather than `label`: the edge draws its
          // own label now, beside the button that removes it.
          data: {
            countLabel: telemetry
              ? tn("canvas.apdu", telemetry.apdus, {
                  count: telemetry.apdus.toLocaleString(),
                })
              : undefined,
            oob,
          },
        };
      }),
    // `tn` is in here because it is what reads the locale, and nothing else in
    // this list changes when the language does. docs/languages.md warns about
    // exactly this, and then this memo did it: every edge kept its English
    // count until something else happened to invalidate the memo.
    [graph.edges, edgeTelemetry, running, selectedEdges, tn],
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
        refuse(verdict.reason ?? t("canvas.refused"));
        return;
      }
      connect(from, to);
    },
    [graph, connect, refuse, t],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<Node<PlexusNodeData>>[]) => {
      const removed: string[] = [];
      const selects: { id: string; selected: boolean }[] = [];

      for (const change of changes) {
        if (change.type === "position" && change.position) {
          // Dragging a group emits one of these per node, every frame.
          moveNode(change.id, change.position, change.dragging === false);
        } else if (change.type === "remove") {
          removed.push(change.id);
        } else if (change.type === "select") {
          selects.push({ id: change.id, selected: change.selected });
        }
      }

      // Batched: deleting six nodes should be one undo step, not six.
      if (selects.length) applySelection(selects);
      if (removed.length) removeNodes(removed);
    },
    [moveNode, removeNodes, applySelection],
  );

  const onEdgesDelete = useCallback(
    (edges: Edge[]) => edges.forEach((e) => disconnect(e.id)),
    [disconnect],
  );

  /**
   * Edge selection, which the canvas never applied.
   *
   * Without this React Flow's edge changes went nowhere, so an edge could not
   * become selected — and Delete, which acts on the selection, always took a
   * node instead. The only way to remove a wire was to delete a node on the end
   * of it. Removals are left to `onEdgesDelete`, which already tells the store;
   * applying them here as well would disconnect each edge twice.
   */
  const onEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => {
    const selects = changes.filter(
      (c): c is Extract<EdgeChange<Edge>, { type: "select" }> => c.type === "select",
    );
    if (selects.length === 0) return;
    setSelectedEdges((current) => {
      const next = new Set(current);
      for (const change of selects) {
        if (change.selected) next.add(change.id);
        else next.delete(change.id);
      }
      return next;
    });
  }, []);

  /**
   * Drop a node onto the canvas, centred on where it was aimed.
   *
   * The one placement path — the palette's click, its desktop drag and its
   * touch drag all reach here, so all three respect the current pan and zoom.
   * A bare click aims at the visible centre and fans successive adds a little,
   * so clicking a type five times gives five nodes you can tell apart rather
   * than a pile.
   */
  const place = useCallback(
    (key: string, at: DropTarget) => {
      if (!nodeType(key)) return;
      const el = wrapper.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();

      let point: { x: number; y: number };
      if (at === "center") {
        const fan = (useGraph.getState().graph.nodes.length % 6) * 28;
        point = { x: rect.left + rect.width / 2 + fan, y: rect.top + rect.height / 2 + fan };
      } else {
        point = at;
      }

      const f = screenToFlowPosition(point);
      addNode(key, {
        x: Math.round(f.x - NODE_WIDTH / 2),
        y: Math.round(f.y - NODE_HEIGHT / 2),
      });
    },
    [addNode, screenToFlowPosition],
  );

  /**
   * The `+` on a free port: offer what could answer it, then wire it up.
   *
   * "Could answer" is the edge rule rather than a list — same channel, opposite
   * role — so a type that becomes connectable by gaining a port becomes
   * offered here without anything being added to a table. The new node lands
   * clear of the one it was asked from, on the side the conversation travels.
   */
  const quickAdd = useCallback(
    (from: { node: string; port: string; at: { x: number; y: number } }) => {
      const source = useGraph.getState().graph.nodes.find((n) => n.id === from.node);
      const port = source && nodeType(source.type)?.ports.find((p) => p.id === from.port);
      if (!source || !port) return;

      const candidates = NODE_TYPES.flatMap((type) => {
        const match = type.ports.find(
          (other) => other.channel === port.channel && other.role !== port.role,
        );
        return match ? [{ type, port: match }] : [];
      });

      const items: MenuItem[] = CLASS_ORDER.flatMap((cls) => {
        const inClass = candidates.filter((c) => c.type.nodeClass === cls);
        if (inClass.length === 0) return [];
        return [
          { key: `h-${cls}`, label: classLabel(cls), disabled: true },
          ...inClass.map(({ type, port: target }) => ({
            key: `${cls}-${type.key}`,
            label: nodeTitle(type),
            onSelect: () => {
              // Placed a card's width clear of the source, on the side the
              // conversation travels: a requester asks rightward.
              const beside = {
                x: source.position.x + (port.role === "requester" ? NODE_WIDTH + 90 : -(NODE_WIDTH + 90)),
                y: source.position.y,
              };
              const made = addNode(type.key, beside);
              const ends =
                port.role === "requester"
                  ? [{ node: from.node, port: from.port }, { node: made, port: target.id }]
                  : [{ node: made, port: target.id }, { node: from.node, port: from.port }];
              const verdict = canConnect(useGraph.getState().graph, ends[0], ends[1]);
              if (verdict.ok) connect(ends[0], ends[1]);
              else refuse(verdict.reason ?? "");
            },
          })),
        ];
      });

      setMenu({ x: from.at.x, y: from.at.y, items });
    },
    [addNode, connect, refuse],
  );

  const canvasRect = useCallback(
    () => wrapper.current?.getBoundingClientRect() ?? null,
    [],
  );

  // Hand the placement path to the palette, which sits outside this provider.
  useEffect(() => {
    usePlacement.getState().register(place, canvasRect, quickAdd);
    return () => usePlacement.getState().register(null, null, null);
  }, [place, canvasRect, quickAdd]);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const key = event.dataTransfer.getData(PALETTE_DRAG_TYPE);
      if (!key) return;
      place(key, { x: event.clientX, y: event.clientY });
    },
    [place],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  // ── The right-click menu ──────────────────────────────────────────────────
  //
  // Opened from React Flow's own context-menu callbacks, which fire on a
  // right-click and on a touch long-press alike, and from a node's `+`. The
  // canvas decides what each menu says; `ContextMenu` only lays it out.

  const closeMenu = useCallback(() => setMenu(null), []);

  /** Bring the inspector into view for a node, wherever it is laid out. */
  const configure = useCallback(
    (id: string) => {
      select(id);
      if (!useLayout.getState().inspector) useLayout.getState().toggle("inspector");
    },
    [select],
  );

  /** The palette, as a nested "Add node" submenu that drops at the cursor. */
  const addNodeSubmenu = useCallback(
    (at: { x: number; y: number }): MenuItem[] =>
      CLASS_ORDER.flatMap((cls) => {
        const items = NODE_TYPES.filter((ty) => ty.nodeClass === cls);
        if (items.length === 0) return [];
        return [
          { key: `h-${cls}`, label: classLabel(cls), disabled: true },
          ...items.map((ty) => ({
            key: ty.key,
            label: nodeTitle(ty),
            onSelect: () =>
              addNode(ty.key, { x: Math.round(at.x - 90), y: Math.round(at.y - 40) }),
          })),
        ];
      }),
    [addNode],
  );

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node<PlexusNodeData>) => {
      event.preventDefault();
      const id = (node.data as PlexusNodeData).nodeId;
      // Act on the whole selection when the node is part of it; otherwise on
      // just this node, and make it the selection so the menu's target is what
      // the person sees highlighted.
      const ids = selection.includes(id) ? selection : [id];
      if (!selection.includes(id)) select(id);

      const model = graph.nodes.find((n) => n.id === id);
      const many = ids.length > 1;

      const items: MenuItem[] = many
        ? [
            { key: "dup", label: t("menu.duplicateN", { n: ids.length }), onSelect: () => duplicate(ids) },
            { key: "copy", label: t("menu.copyN", { n: ids.length }), onSelect: () => copy(ids) },
            { key: "s1", separator: true, label: "" },
            { key: "del", label: t("menu.deleteN", { n: ids.length }), danger: true, onSelect: () => removeNodes(ids) },
          ]
        : [
            { key: "config", label: t("menu.configure"), onSelect: () => configure(id) },
            { key: "dup", label: t("menu.duplicate"), onSelect: () => duplicate(ids) },
            { key: "copy", label: t("menu.copy"), onSelect: () => copy(ids) },
            { key: "s1", separator: true, label: "" },
            {
              key: "disable",
              label: model?.disabled ? t("menu.enable") : t("menu.disable"),
              onSelect: () => toggleDisabled(id),
            },
            { key: "s2", separator: true, label: "" },
            { key: "del", label: t("menu.delete"), danger: true, onSelect: () => removeNodes(ids) },
          ];

      setMenu({ x: event.clientX, y: event.clientY, items });
    },
    [selection, select, graph.nodes, t, duplicate, copy, removeNodes, configure, toggleDisabled],
  );

  const onSelectionContextMenu = useCallback(
    (event: React.MouseEvent, nodes: Node<PlexusNodeData>[]) => {
      event.preventDefault();
      const ids = nodes.map((n) => (n.data as PlexusNodeData).nodeId);
      setMenu({
        x: event.clientX,
        y: event.clientY,
        items: [
          { key: "dup", label: t("menu.duplicateN", { n: ids.length }), onSelect: () => duplicate(ids) },
          { key: "copy", label: t("menu.copyN", { n: ids.length }), onSelect: () => copy(ids) },
          { key: "s1", separator: true, label: "" },
          { key: "del", label: t("menu.deleteN", { n: ids.length }), danger: true, onSelect: () => removeNodes(ids) },
        ],
      });
    },
    [t, duplicate, copy, removeNodes],
  );

  const onPaneContextMenu = useCallback(
    (event: MouseEvent | React.MouseEvent) => {
      event.preventDefault();
      const at = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      setMenu({
        x: event.clientX,
        y: event.clientY,
        items: [
          {
            key: "paste",
            label: t("menu.paste"),
            disabled: !clipboard,
            onSelect: () => paste(at),
          },
          { key: "add", label: t("menu.addNode"), submenu: addNodeSubmenu(at) },
          { key: "s1", separator: true, label: "" },
          { key: "all", label: t("menu.selectAll"), disabled: graph.nodes.length === 0, onSelect: selectAll },
          { key: "fit", label: t("menu.fitView"), disabled: graph.nodes.length === 0, onSelect: () => void fitView({ duration: 200 }) },
        ],
      });
    },
    [screenToFlowPosition, t, clipboard, paste, addNodeSubmenu, graph.nodes.length, selectAll, fitView],
  );

  return (
    <div className="canvas" ref={wrapper} onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onEdgesDelete={onEdgesDelete}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onInit={(instance) => {
          // Handy from the browser console while developing; not shipped.
          if (import.meta.env.DEV) {
            (window as unknown as Record<string, unknown>).__rf = instance;
          }
        }}
        onPaneClick={() => {
          select(null);
          setSelectedEdges(new Set());
        }}
        onNodeContextMenu={onNodeContextMenu}
        onSelectionContextMenu={onSelectionContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        // React Flow is MIT-licensed and this flag is its own supported way to
        // hide the badge, so nothing is being defeated by using it. The library
        // is still credited by name in the README's stack list; this only
        // removes the link that sat over the graph.
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: "plexus" }}
        minZoom={0.25}
        maxZoom={2}
        // Stated rather than left to the defaults, because they are the whole
        // of "the work zone zooms": the wheel and a trackpad pinch on a
        // desktop, two fingers on a touch screen, a double-tap either way, and
        // the +/− controls bottom-left for anyone who wants a button.
        zoomOnScroll
        zoomOnPinch
        zoomOnDoubleClick
        deleteKeyCode={["Backspace", "Delete"]}
        selectionOnDrag={mode === "select" && !coarse}
        panOnDrag={coarse || mode !== "select" ? true : [1, 2]}
        selectionMode={SelectionMode.Partial}
        selectNodesOnDrag={false}
        multiSelectionKeyCode={["Meta", "Control", "Shift"]}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={18}
          size={1}
          color="var(--canvas-grid-major)"
        />
        <Controls showInteractive={false} />

        {/* The overview and its controls. Class-coloured, so it shows the shape
            of the chain and not four identical blocks. Open, minimised to a
            labelled pill, or hidden to a single button — remembered across
            reloads. */}
        {minimap === "open" && (
          <>
            <MiniMap
              pannable
              zoomable
              ariaLabel={t("canvas.overview")}
              nodeClassName={minimapClass}
              nodeStrokeWidth={0}
            />
            <Panel
              position="bottom-right"
              className={`minimap-bar minimap-bar--float${
                mapHover || coarse ? " is-visible" : ""
              }`}
            >
              <button
                type="button"
                className="minimap-bar__btn"
                onClick={() => setMinimap("mini")}
                title={t("canvas.minimap.minimize")}
                aria-label={t("canvas.minimap.minimize")}
              >
                <Icon name="minus" />
              </button>
              <button
                type="button"
                className="minimap-bar__btn"
                onClick={() => setMinimap("hidden")}
                title={t("canvas.minimap.hide")}
                aria-label={t("canvas.minimap.hide")}
              >
                <Icon name="close" />
              </button>
            </Panel>
          </>
        )}

        {minimap === "mini" && (
          <Panel position="bottom-right" className="minimap-bar minimap-bar--dock">
            <button
              type="button"
              className="minimap-bar__label"
              onClick={() => setMinimap("open")}
              title={t("canvas.minimap.expand")}
            >
              <Icon name="window" />
              <span>{t("canvas.overview")}</span>
            </button>
            <button
              type="button"
              className="minimap-bar__btn"
              onClick={() => setMinimap("hidden")}
              title={t("canvas.minimap.hide")}
              aria-label={t("canvas.minimap.hide")}
            >
              <Icon name="close" />
            </button>
          </Panel>
        )}

        {minimap === "hidden" && (
          <Panel position="bottom-right" className="minimap-bar minimap-bar--dock">
            <button
              type="button"
              className="minimap-bar__btn"
              onClick={() => setMinimap("open")}
              title={t("canvas.minimap.show")}
              aria-label={t("canvas.minimap.show")}
            >
              <Icon name="window" />
            </button>
          </Panel>
        )}

        <Panel position="top-left" className="tools">
          {/* Not offered to a finger: with no second pointer to pan with,
              select mode would be pan mode wearing another label. Tapping a
              node still selects it. */}
          {!coarse && (
            <div className="tools__group" role="group" aria-label={t("canvas.tool")}>
              <button
                type="button"
                className={`tools__btn${mode === "pan" ? " is-on" : ""}`}
                onClick={() => setMode("pan")}
                aria-pressed={mode === "pan"}
                title={t("canvas.pan.title")}
              >
                {t("canvas.pan")}
              </button>
              <button
                type="button"
                className={`tools__btn${mode === "select" ? " is-on" : ""}`}
                onClick={() => setMode("select")}
                aria-pressed={mode === "select"}
                title={t("canvas.select.title")}
              >
                {t("canvas.select")}
              </button>
            </div>
          )}

          {selection.length > 0 && (
            <div className="tools__group tools__group--selection">
              <span className="tools__count">
                {tn("canvas.selected", selection.length)}
              </span>
              <button
                type="button"
                className="tools__btn tools__btn--danger"
                onClick={() => removeNodes(selection)}
                title={t("canvas.delete.title")}
              >
                {t("canvas.delete")}
              </button>
              <button
                type="button"
                className="tools__btn"
                onClick={() => select(null)}
                title={t("canvas.clear.title")}
              >
                {t("canvas.clear")}
              </button>
            </div>
          )}
        </Panel>
      </ReactFlow>

      {graph.nodes.length === 0 && (
        <div className="canvas__hint">
          <p>{t("canvas.empty.title")}</p>
          <p className="canvas__hint-sub">{t("canvas.empty.hint")}</p>
        </div>
      )}

      {refusal && (
        <div className="canvas__refusal" role="status">
          {refusal}
        </div>
      )}

      {menu && <ContextMenu state={menu} onClose={closeMenu} />}
    </div>
  );
}

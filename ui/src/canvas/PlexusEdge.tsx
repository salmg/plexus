/**
 * The wire, and the one control it needs.
 *
 * A Plexus edge is a conversation rather than a line, so removing one is a real
 * edit — and until this existed there was no way to make it. Edges could not be
 * selected (the canvas never applied React Flow's edge changes), so Delete
 * always took whatever node was selected instead, and the only way to get rid
 * of a wire was to delete a node on the end of it and draw the rest again.
 *
 * So the wire carries its own delete, the way n8n does: hover it and a button
 * appears at its midpoint. Hover is tracked here rather than in the canvas
 * because the alternative — one `hoveredEdge` in canvas state — rebuilds every
 * edge on every mouse move across the pane.
 *
 * It also shows on `selected`, so the button is reachable by clicking the wire
 * and by keyboard, not only by holding a mouse still over it.
 */

import { useState } from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath, Position } from "@xyflow/react";
import type { EdgeProps } from "@xyflow/react";

import { useT } from "../i18n";
import { useGraph } from "../store/useGraph";

export interface PlexusEdgeData extends Record<string, unknown> {
  /** The exchange count the runtime reports, already translated. */
  countLabel?: string;
  /** Out-of-band: a side channel to the card rather than a step in the chain. */
  oob?: boolean;
}

/** How far short of the port the wire stops, leaving room for the arrowhead. */
const ARROW_GAP = 7;

/** Move a point back along the direction the wire arrives from. */
function shortenBy(
  x: number,
  y: number,
  side: Position,
  gap: number,
): [number, number] {
  if (side === Position.Left) return [x - gap, y];
  if (side === Position.Right) return [x + gap, y];
  if (side === Position.Top) return [x, y - gap];
  return [x, y + gap];
}

export function PlexusEdgeView({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  data,
  selected,
}: EdgeProps) {
  const t = useT();
  const disconnect = useGraph((s) => s.disconnect);
  const [hovered, setHovered] = useState(false);

  // The wire stops short of the port so the arrowhead has somewhere to sit.
  // Drawn to the handle itself, the tip lands on top of the port square and
  // the two read as one smudge.
  const [endX, endY] = shortenBy(targetX, targetY, targetPosition, ARROW_GAP);

  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX: endX,
    targetY: endY,
    targetPosition,
  });

  const info = (data ?? {}) as PlexusEdgeData;
  const show = hovered || !!selected;

  return (
    <>
      {/* The handlers sit on a group around both paths so moving along the
          wire does not flicker between them. */}
      <g
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <BaseEdge id={id} path={path} style={style} markerEnd={markerEnd} />
        {/* A fat transparent path: a 2px wire is a target you have to aim at,
            and the button only appears once you have hit it. */}
        <path d={path} className="pedge__hit" />
      </g>

      <EdgeLabelRenderer>
        <div
          className={`pedge__tools${show ? " pedge__tools--on" : ""}`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          {info.countLabel && <span className="pedge__count">{info.countLabel}</span>}
          <button
            type="button"
            className="pedge__cut"
            title={t("edge.remove")}
            aria-label={t("edge.remove")}
            onClick={(e) => {
              e.stopPropagation();
              disconnect(id);
            }}
          >
            ✕
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

/**
 * One node on the canvas.
 *
 * Ports are drawn where their role puts them and nowhere else: a responder on
 * the left because something asks it from that side, a requester on the right
 * because that is where it goes looking.  An out-of-band requester hangs off
 * the underside, which is the honest place for a wire that skips the
 * conversation.
 */

import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import { nodeType } from "../catalogue";
import type { NodeTelemetry, PortSpec } from "../graph/types";
import { useGraph } from "../store/useGraph";
import { useRuntime } from "../store/useRuntime";

export interface PlexusNodeData extends Record<string, unknown> {
  nodeId: string;
}

/**
 * Node cards are a fixed size, declared here and pinned in canvas.css to match.
 *
 * React Flow can measure a node itself, but only if the object you hand it
 * carries the measurement forward on the next render — and here the nodes are
 * derived fresh from the store, so every render would wipe what it just
 * measured and no node would ever count as initialised.  Declaring the size
 * removes the round trip: fitView, the minimap and edge routing all have real
 * bounds from the first frame.  Keep these two numbers and the CSS in step.
 */
export const NODE_WIDTH = 184;
export const NODE_HEIGHT = 112;

const STATUS_CLASS: Record<NodeTelemetry["status"], string> = {
  idle: "",
  ready: "dot--warn",
  live: "dot--live",
  error: "dot--danger",
};

export function PlexusNodeView({ data, selected }: NodeProps) {
  const { nodeId } = data as PlexusNodeData;

  const node = useGraph((s) => s.graph.nodes.find((n) => n.id === nodeId));
  const devices = useGraph((s) => s.devices);
  const telemetry = useRuntime((s) => s.nodes[nodeId]);

  if (!node) return null;
  const type = nodeType(node.type);
  if (!type) {
    return (
      <div className="pnode pnode--broken">
        <div className="pnode__title">Unknown type</div>
        <div className="pnode__id">{node.type}</div>
      </div>
    );
  }

  const responders = type.ports.filter((p) => p.role === "responder");
  const requesters = type.ports.filter((p) => p.role === "requester" && !p.oob);
  const oob = type.ports.filter((p) => p.oob);

  const deviceField = type.fields.find((f) => f.kind === "device");
  const boundId = deviceField ? node.config[deviceField.key] : undefined;
  const bound =
    typeof boundId === "string" ? devices.find((d) => d.id === boundId) : undefined;

  const classes = [
    "pnode",
    `pnode--${type.nodeClass}`,
    selected ? "is-selected" : "",
    node.disabled ? "is-disabled" : "",
    telemetry?.status === "live" ? "is-live" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes} data-testid={`node-${node.id}`}>
      <header className="pnode__head">
        <span className="pnode__title">{node.label || type.title}</span>
        <span className={`dot ${telemetry ? STATUS_CLASS[telemetry.status] : ""}`} />
      </header>

      <div className="pnode__id">{node.id}</div>

      <div className="pnode__body">
        {deviceField && (
          <div className={`pnode__bind ${bound ? "" : "is-missing"}`}>
            {bound ? bound.name : (boundId as string) || "no device"}
          </div>
        )}
        {!deviceField && <div className="pnode__bind">{summarise(node.config)}</div>}

        {telemetry && telemetry.apdus > 0 ? (
          <div className="pnode__stats">
            <span>{telemetry.apdus.toLocaleString()} apdu</span>
            {telemetry.meanMs !== null && <span>{telemetry.meanMs.toFixed(1)} ms</span>}
            {telemetry.errors > 0 && (
              <span className="is-error">{telemetry.errors} err</span>
            )}
          </div>
        ) : (
          <div className="pnode__stats pnode__stats--idle">
            <span>{type.latencyMs} ms budget</span>
          </div>
        )}
      </div>

      {responders.map((port, i) => (
        <PortHandle key={port.id} port={port} index={i} count={responders.length} />
      ))}
      {requesters.map((port, i) => (
        <PortHandle key={port.id} port={port} index={i} count={requesters.length} />
      ))}
      {oob.map((port) => (
        <PortHandle key={port.id} port={port} index={0} count={1} />
      ))}
    </div>
  );
}

function PortHandle({
  port,
  index,
  count,
}: {
  port: PortSpec;
  index: number;
  count: number;
}) {
  // React Flow's source end is the one a drag starts from, and a Plexus edge is
  // always drawn from the side that asks, so requester maps to source.
  const isRequester = port.role === "requester";
  const position = port.oob
    ? Position.Bottom
    : isRequester
      ? Position.Right
      : Position.Left;

  const offset = `${((index + 1) / (count + 1)) * 100}%`;
  const style = port.oob ? { left: offset } : { top: offset };

  return (
    <>
      <Handle
        id={port.id}
        type={isRequester ? "source" : "target"}
        position={position}
        className={`pport pport--${port.channel} pport--${port.role}${port.oob ? " pport--oob" : ""}`}
        style={style}
      />
      <span
        className={`pport__label pport__label--${port.oob ? "oob" : isRequester ? "right" : "left"}`}
        style={port.oob ? undefined : { top: offset }}
        aria-hidden="true"
      >
        {port.label}
      </span>
    </>
  );
}

/** A one-line hint of what a node without a device is pointed at. */
function summarise(config: Record<string, unknown>): string {
  const host = config.host;
  const port = config.port;
  if (typeof host === "string" && host) {
    return typeof port === "number" ? `${host}:${port}` : host;
  }
  const path = config.path;
  if (typeof path === "string" && path) return path;
  return "—";
}

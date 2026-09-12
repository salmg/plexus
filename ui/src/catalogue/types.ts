/**
 * How a node type describes itself.
 *
 * The catalogue is data, never code.  The palette, the inspector form, the
 * connection validator and the latency estimate are all generated from these
 * records, so adding a node means adding an entry here and an adapter in the
 * runtime — never a new React component.
 */

import type { DeviceKind, NodeClass, PortSpec } from "../graph/types.ts";
import type { IconName } from "./icons.tsx";

export interface SelectOption {
  value: string;
  label: string;
}

interface FieldBase {
  key: string;
  label: string;
  help?: string;
  /** Hide this field unless another field holds a given value. */
  showWhen?: { key: string; equals: unknown };
  /**
   * What this field makes the graph reach for, when it reaches beyond itself.
   *
   * Read by `describeIntent`, which builds the sheet shown before a graph
   * somebody sent you is opened. That used to guess from the key name —
   * anything called `path` was a file, anything called `host` was an address —
   * so `cafile` was a file the graph opened and the sheet never mentioned.
   * Declared here instead, because the catalogue is where a node's facts live
   * and a guess is not a fact.
   */
  reaches?: "file" | "address";
}

export type FieldSpec =
  | (FieldBase & { kind: "text"; placeholder?: string; default?: string })
  | (FieldBase & { kind: "hex"; placeholder?: string; default?: string })
  | (FieldBase & {
      kind: "number";
      min?: number;
      max?: number;
      step?: number;
      default?: number;
    })
  | (FieldBase & {
      kind: "select";
      options: SelectOption[];
      default?: string;
    })
  | (FieldBase & { kind: "bool"; default?: boolean })
  | (FieldBase & { kind: "device"; deviceKind: DeviceKind })
  | (FieldBase & { kind: "yaml"; default?: string; rows?: number });

export interface NodeType {
  /** Stable catalogue key, e.g. `acr122u.target`.  Persisted in the graph. */
  key: string;
  /** Which glyph from `icons.tsx` stands for this node. */
  icon: IconName;
  title: string;
  nodeClass: NodeClass;
  /** One line, shown under the title in the palette and the inspector. */
  summary: string;
  ports: PortSpec[];
  fields: FieldSpec[];
  /**
   * Milliseconds this node is expected to add to a round trip.  Rough, and
   * deliberately so — it exists to answer "will this graph survive a tap?"
   * before hardware is plugged in, not to be a benchmark.
   */
  latencyMs: number;
  /** Physical device this node claims exclusively, when it claims one. */
  device?: DeviceKind;
  /** Things that are true about this node and change how you read a result. */
  caveats?: string[];
}

/** The two apdu ports, spelled once. */
export const CARD_PORT: PortSpec = {
  id: "card",
  channel: "apdu",
  role: "requester",
  label: "card",
};

export const TERMINAL_PORT: PortSpec = {
  id: "terminal",
  channel: "apdu",
  role: "responder",
  label: "terminal",
};

export const PASSTHROUGH_PORTS: PortSpec[] = [TERMINAL_PORT, CARD_PORT];

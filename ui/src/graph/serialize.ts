/**
 * Reading and writing `.plexus.yaml`.
 *
 * The file is the source of truth: the editor is one way to produce it, the
 * runtime is one way to consume it, and neither owns it.  A mutation node's
 * `playbook` config is a YAML document in its own right, and js-yaml emits it
 * as a block scalar, so an atrium playbook pasted into a node still reads as a
 * playbook in the saved file.
 *
 * Loading is deliberately strict.  A graph file names hardware and opens
 * sockets, so anything unrecognised is refused rather than half-applied.
 */

import { dump, load } from "js-yaml";

import { nodeType } from "../catalogue";
import type { PlexusEdge, PlexusGraph, PlexusNode } from "./types";

export const FILE_EXTENSION = ".plexus.yaml";

export function toYaml(graph: PlexusGraph): string {
  const doc = {
    version: graph.version,
    name: graph.name,
    ...(graph.hosts.length ? { hosts: graph.hosts } : {}),
    nodes: graph.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      ...(n.label ? { label: n.label } : {}),
      ...(n.host ? { host: n.host } : {}),
      ...(n.disabled ? { disabled: true } : {}),
      at: [Math.round(n.position.x), Math.round(n.position.y)],
      ...(Object.keys(n.config).length ? { config: n.config } : {}),
    })),
    edges: graph.edges.map((e) => ({
      id: e.id,
      from: `${e.from.node}:${e.from.port}`,
      to: `${e.to.node}:${e.to.port}`,
    })),
  };

  const body = dump(doc, { lineWidth: 88, noRefs: true, sortKeys: false });
  return `${HEADER}\n${body}`;
}

const HEADER = `# Plexus graph.
#
# Edges read "from: node:port" to "to: node:port" — always from the side that
# asks to the side that answers, which is the direction a command APDU travels.
# Responses come back along the same edge.`;

/**
 * A file this build will not read, and why.
 *
 * Carries a message key rather than a sentence. The `message` is still English
 * and still useful in a console or a stack trace, but the one place a person
 * actually meets one of these — the alert when they open a `.plexus.yaml`
 * somebody sent them — is the last place an untranslated string belongs, and
 * for a while it was the only untranslated string the interface had.
 *
 * `plexus/graph.py` raises the same conditions with the same wording; keeping
 * the English here is what makes the two comparable.
 */
export class GraphParseError extends Error {
  readonly key: string;
  readonly vars: Record<string, string | number>;

  constructor(key: string, vars: Record<string, string | number>, english: string) {
    super(english);
    this.key = key;
    this.vars = vars;
  }
}

export function fromYaml(text: string): PlexusGraph {
  let raw: unknown;
  try {
    raw = load(text);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new GraphParseError(
      "parse.notYaml",
      { detail },
      `Not valid YAML: ${detail}`,
    );
  }

  if (!isRecord(raw)) {
    throw new GraphParseError("parse.empty", {}, "The file is empty.");
  }
  if (raw.version !== 1) {
    throw new GraphParseError(
      "parse.version",
      { version: String(raw.version) },
      `Unsupported graph version ${String(raw.version)}. This build reads version 1.`,
    );
  }

  const nodes = parseNodes(raw.nodes);
  const known = new Set(nodes.map((n) => n.id));

  return {
    version: 1,
    name: typeof raw.name === "string" && raw.name ? raw.name : "untitled",
    hosts: parseHosts(raw.hosts),
    nodes,
    edges: parseEdges(raw.edges, known),
  };
}

function parseNodes(value: unknown): PlexusNode[] {
  if (!Array.isArray(value)) {
    throw new GraphParseError("parse.nodesList", {}, "`nodes` must be a list.");
  }

  const seen = new Set<string>();
  return value.map((entry, i) => {
    if (!isRecord(entry)) {
      throw new GraphParseError(
        "parse.nodeMapping",
        { index: i },
        `Node ${i} is not a mapping.`,
      );
    }

    const id = requireString(entry.id, `nodes[${i}].id`);
    if (seen.has(id)) {
      throw new GraphParseError(
        "parse.duplicateId",
        { id },
        `Two nodes share the id "${id}".`,
      );
    }
    seen.add(id);

    const type = requireString(entry.type, `nodes[${i}].type`);
    if (!nodeType(type)) {
      throw new GraphParseError(
        "parse.unknownType",
        { id, type },
        `Node "${id}" has type "${type}", which this build does not know.`,
      );
    }

    const at = entry.at;
    const position =
      Array.isArray(at) && typeof at[0] === "number" && typeof at[1] === "number"
        ? { x: at[0], y: at[1] }
        : { x: 0, y: 0 };

    return {
      id,
      type,
      position,
      config: isRecord(entry.config) ? entry.config : {},
      ...(typeof entry.label === "string" ? { label: entry.label } : {}),
      ...(typeof entry.host === "string" ? { host: entry.host } : {}),
      ...(entry.disabled === true ? { disabled: true } : {}),
    };
  });
}

function parseEdges(value: unknown, known: Set<string>): PlexusEdge[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    throw new GraphParseError("parse.edgesList", {}, "`edges` must be a list.");
  }

  return value.map((entry, i) => {
    if (!isRecord(entry)) {
      throw new GraphParseError(
        "parse.edgeMapping",
        { index: i },
        `Edge ${i} is not a mapping.`,
      );
    }
    const from = parseRef(entry.from, `edges[${i}].from`, known);
    const to = parseRef(entry.to, `edges[${i}].to`, known);
    const id =
      typeof entry.id === "string" && entry.id
        ? entry.id
        : `${from.node}:${from.port}->${to.node}:${to.port}`;
    return { id, from, to };
  });
}

function parseRef(value: unknown, where: string, known: Set<string>) {
  const text = requireString(value, where);
  const at = text.lastIndexOf(":");
  if (at <= 0 || at === text.length - 1) {
    throw new GraphParseError(
      "parse.portRef",
      { where, text },
      `${where} should read "node:port", got "${text}".`,
    );
  }
  const node = text.slice(0, at);
  if (!known.has(node)) {
    throw new GraphParseError(
      "parse.unknownNode",
      { where, node },
      `${where} names node "${node}", which is not defined.`,
    );
  }
  return { node, port: text.slice(at + 1) };
}

function parseHosts(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((h, i) => ({
    id: requireString(h.id, `hosts[${i}].id`),
    name: typeof h.name === "string" ? h.name : requireString(h.id, `hosts[${i}].id`),
    ...(typeof h.address === "string" ? { address: h.address } : {}),
    ...(typeof h.note === "string" ? { note: h.note } : {}),
  }));
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function requireString(v: unknown, where: string): string {
  if (typeof v !== "string" || v === "") {
    throw new GraphParseError(
      "parse.requireString",
      { where },
      `${where} must be a non-empty string.`,
    );
  }
  return v;
}

/**
 * What a graph will reach for if it runs: every device it binds and every
 * address it dials.  Shown before an imported graph can be armed, because a
 * graph file is untrusted input that can claim hardware and open sockets.
 */
export interface GraphIntent {
  devices: string[];
  addresses: string[];
  files: string[];
}

export function describeIntent(graph: PlexusGraph): GraphIntent {
  const devices: string[] = [];
  const addresses: string[] = [];
  const files: string[] = [];

  for (const node of graph.nodes) {
    const type = nodeType(node.type);
    if (!type) continue;

    for (const field of type.fields) {
      const value = node.config[field.key];
      if (typeof value !== "string" || value === "") continue;
      if (field.kind === "device") devices.push(value);
      if (field.key === "path") files.push(value);
    }

    const host = node.config.host;
    const port = node.config.port;
    if (typeof host === "string" && host !== "") {
      addresses.push(typeof port === "number" ? `${host}:${port}` : host);
    }
  }

  return {
    devices: [...new Set(devices)],
    addresses: [...new Set(addresses)],
    files: [...new Set(files)],
  };
}

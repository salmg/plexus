/**
 * The chrome, in English. This file is the key list.
 *
 * Node titles, summaries, caveats and field labels are *not* here: they live in
 * the catalogue, which is also what the Python side reads. Adding them would
 * give the project two Englishes to keep in step. See `catalogueText`.
 *
 * Counted messages carry `_one` and `_other` as whole phrases rather than a
 * number glued to a word, because not every language puts the number where
 * English does.
 */

export const en = {
  // ── Top bar ──────────────────────────────────────────────────────────
  "app.tagline": "node canvas for atrium",
  "app.by": "Designed and provided by academy.salmg.net",
  "topbar.undo": "Undo",
  "topbar.redo": "Redo",
  "topbar.open": "Open",
  "topbar.save": "Save",
  "topbar.new": "New",
  "topbar.console": "Console",
  "topbar.inspector": "Inspector",
  "topbar.palette": "Palette",
  "topbar.more": "More controls",
  "topbar.run": "Run",
  "topbar.stop": "Stop",
  "topbar.console.title": "Show or hide the console",
  "topbar.inspector.title": "Show or hide the inspector",
  "topbar.palette.title": "Show or hide the node palette",
  "layout.close": "Close this panel",
  "topbar.runtime.title": "Where the graph would actually run",
  "runtime.confirmRemote":
    "This link points the editor at a runtime on {host}, which is not this machine.\n\nWhatever is there receives every graph you run — the readers and files it names — and can write into your console.\n\nUse it?",
  "topbar.run.blocked": "Fix the errors in the checks list first",
  "topbar.run.ready": "Bind devices and start",
  "topbar.theme.toLight": "Switch to light mode",
  "topbar.theme.toDark": "Switch to dark mode",
  "topbar.language": "Language",
  "topbar.unreadable": "Could not read that file: {message}",
  "count.errors_one": "1 error",
  "count.errors_other": "{count} errors",
  "count.warnings_one": "1 warning",
  "count.warnings_other": "{count} warnings",

  // ── Palette ──────────────────────────────────────────────────────────
  "palette.title": "Palette",
  "palette.filter": "Filter nodes",
  "palette.empty": "No node matches “{query}”.",
  "palette.needs": "Needs {needs}. This runtime cannot bind it.",
  "palette.unavailable":
    "This runtime cannot bind this node. It needs {needs}. You can still draw it — the file outlives whichever runtime is answering.",
  "palette.class.terminal": "Terminal side",
  "palette.class.transform": "Transforms",
  "palette.class.card": "Card side",
  "palette.class.host": "Host layer",
  "legend.cardPort": "card port — wants a card",
  "legend.cardPort.title": "A requester: it needs a card on the other end",
  "legend.terminalPort": "terminal port — is a card",
  "legend.terminalPort.title": "A responder: it behaves as a card would",

  // ── Canvas ───────────────────────────────────────────────────────────
  "canvas.pan": "Pan",
  "canvas.pan.title": "Drag the canvas to pan",
  "canvas.select": "Select",
  "canvas.select.title": "Drag a box to select nodes. Middle or right button still pans.",
  "canvas.tool": "Mouse tool",
  "canvas.overview": "Graph overview",
  "canvas.selected_one": "1 selected",
  "canvas.selected_other": "{count} selected",
  "canvas.delete": "Delete",
  "canvas.delete.title": "Delete the selected nodes",
  "canvas.clear": "Clear",
  "canvas.clear.title": "Clear the selection",
  "canvas.empty.title": "Drag a node from the palette.",
  "canvas.empty.hint":
    "A chain runs left to right: something that asks, any number of transforms, then something that answers.",
  "canvas.refused": "That connection is not allowed.",
  "canvas.apdu_one": "1 apdu",
  "canvas.apdu_other": "{count} apdu",

  // ── Node card ────────────────────────────────────────────────────────
  "node.unknown": "Unknown type",
  "node.noDevice": "no device",
  "node.budget": "{ms} ms budget",
  "node.errors": "{count} err",
  "node.status.idle": "not bound",
  "node.status.ready": "bound, nothing crossing",
  "node.status.live": "traffic crossing now",
  "node.status.error": "faulted — see the checks",

  // ── Inspector ────────────────────────────────────────────────────────
  "inspector.title": "Inspector",
  "inspector.graph": "Graph",
  "inspector.nodes_one": "1 node",
  "inspector.nodes_other": "{count} nodes",
  "inspector.name": "Name",
  "inspector.roundTrip": "{ms} ms out and back",
  "inspector.hint":
    "Select a node to configure it. Estimates above are the sum of each node's declared cost, not a measurement.",
  "inspector.label": "Label",
  "inspector.host": "Runs on",
  "inspector.hostNone": "this machine",
  "inspector.id": "id",
  "inspector.cost": "cost",
  "inspector.cost.value": "{ms} ms each way",
  "inspector.seen": "seen",
  "inspector.seen.value": "{count} apdu",
  "inspector.seen.withMean": "{count} apdu, {ms} ms mean",
  "inspector.caveats": "Worth knowing",
  "inspector.enable": "Enable",
  "inspector.disable": "Disable",
  "inspector.enableAll": "Enable all",
  "inspector.disableAll": "Disable all",
  "inspector.delete": "Delete",
  "inspector.deleteCount": "Delete {count}",
  "inspector.close": "Close inspector",
  "inspector.multi.hint":
    "Drag any one of them to move the group. Delete removes their wires too.",
  "inspector.unknownType":
    "This graph contains a node of type “{type}”, which this build does not know.",
  "inspector.checks": "Checks",
  "inspector.checks.none": "Nothing to fix. This graph is ready to arm.",
  "field.chooseDevice": "Choose a device",
  "field.notPresent": "{name} — not present",
  "field.noDevices": "No {kind} device is visible to the runtime.",
  "field.hexOnly": "Hex digits only.",

  // ── Console ──────────────────────────────────────────────────────────
  "console.title": "Console",
  "console.streamOn": "Stream on",
  "console.streamOff": "Stream off",
  "console.clear": "Clear",
  "console.banner": 'Plexus console. Type "help".',

  // ── Import review ────────────────────────────────────────────────────
  "import.title": "Review this graph",
  "import.heading": "Load “{name}”?",
  "import.willDo": "If you arm it, it will",
  "import.claim": "claim",
  "import.dial": "dial",
  "import.touch": "read or write",
  "import.nothing": "It binds no hardware, dials nothing and names no files.",
  "import.reassure":
    "Loading it only draws it. Nothing is claimed or dialled until you press Run, and the runtime confines every path to its workspace.",
  "import.cancel": "Cancel",
  "import.load": "Load",

  // ── Checks, written by the validator ─────────────────────────────────
  "check.empty": "The graph is empty. Drag a node from the palette to start.",
  "check.unknownType":
    "Unknown node type “{type}”. This graph was saved by a different version.",
  "check.selfEdge":
    "{node} is wired to itself. A node cannot talk to itself.",
  "check.unknownPort":
    "No port “{port}” on {node}.",
  "check.portContended":
    "The {port} port on {node} is wired twice.",
  "check.cycle":
    "These nodes form a loop: {nodes}. A conversation has to end somewhere.",
  "check.unwiredRequester":
    "{node} has nothing on its {port} port. Wire it to something that answers.",
  "check.twoChains":
    "This graph has {count} separate chains: {nodes}. Plexus runs one at a time, and would run the one starting at {running}.",
  "check.nodeUnavailable":
    "This runtime cannot bind {node}. It needs {needs}.",
  "check.unwiredOob":
    "{node} has nothing on its {port} port, so it will pass traffic through and inject nothing.",
  "check.unwiredResponder": "Nothing is asking {node} anything. It will sit idle.",
  "check.unboundDevice": "{node} has no device selected.",
  "check.deviceMissing": "{name} is not present. Plug it in, or pick another.",
  "check.deviceContended":
    "{nodes} both claim {name}. One device cannot be a target and an initiator at the same time — the failure that comes out of trying is a timeout, not a message.",
  "check.unpairedLink":
    "This edge crosses machines and carries card traffic unauthenticated. Set a pairing secret on {node}, or tunnel it over SSH.",
  "check.tapBudget.warn":
    "About {ms} ms per exchange, out and back. Close to what a contactless kernel will tolerate — expect dropped taps.",

  // ── Why a wire was refused ───────────────────────────────────────────
  "connect.self": "A node cannot talk to itself.",
  "connect.unknownNode": "Node not in this graph.",
  "connect.unknownPort": "Unknown port.",
  "connect.wrongChannel": "A {from} port does not carry {to}.",
  "connect.bothAsk": "Both ports ask. An edge joins one that asks to one that answers.",
  "connect.bothAnswer":
    "Both ports answer. An edge joins one that asks to one that answers.",
  "connect.alreadyWired": "The {port} port on {node} is already wired.",
  "connect.loop": "That would close a loop.",

  // ── Opening a file this build will not read ──────────────────────────
  //
  // The English here is the same wording `plexus/graph.py` raises, so a file
  // refused by the editor and the same file refused by `plexus check` say the
  // same thing.
  "parse.notYaml": "Not valid YAML: {detail}",
  "parse.empty": "The file is empty.",
  "parse.version":
    "Unsupported graph version {version}. This build reads version 1.",
  "parse.nodesList": "`nodes` must be a list.",
  "parse.edgesList": "`edges` must be a list.",
  "parse.nodeMapping": "Node {index} is not a mapping.",
  "parse.edgeMapping": "Edge {index} is not a mapping.",
  "parse.duplicateId": 'Two nodes share the id "{id}".',
  "parse.unknownType":
    'Node "{id}" has type "{type}", which this build does not know.',
  "parse.portRef": '{where} should read "node:port", got "{text}".',
  "parse.unknownNode": '{where} names node "{node}", which is not defined.',
  "parse.requireString": "{where} must be a non-empty string.",
} as const;

/**
 * Every key the chrome uses.
 *
 * Declared here rather than in `index.ts` so a translation can import it
 * without a cycle — which is the whole point of it existing: `es.ts` was typed
 * `Record<string, string>`, so a typo'd key was a silently new key that nothing
 * would ever read, and neither the type system nor a test could tell.
 */
export type MessageKey = keyof typeof en;

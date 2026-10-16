"""
The console the editor types into.

The editor knows nothing about these verbs — it sends a line and prints what
comes back — so a runtime is free to add its own. These are the ones
PROTOCOL.md says every runtime should answer, and they are the reason the
console exists: during a relay the useful thing is rarely reading the trace, it
is reaching into the running process.

Everything here is read-only on the graph. A console that could rewire the rig
would put the editor's document and the running one out of step, and the editor
owns the document.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from plexus.graph import Graph, chain_latency
from plexus.graph import chain as chain_of
from plexus.nodes.transform import MAX_APDU_BYTES
from plexus.runtime import Engine

Result = Tuple[List[str], Optional[str]]  # (lines, level)

HELP = [
    "status            what the runtime is doing",
    "nodes             each node, its state and its counters",
    "edges             each edge and what has crossed it",
    "devices           hardware the runtime can see",
    "tx <hex>          push one command APDU through the chain",
    "release [hex]     let a held breakpoint go, with different bytes or not",
    "pause / resume    hold traffic without unbinding",
    "check             re-run the graph checks",
    "help              this",
]


def run(engine: Engine, graph: Optional[Graph], line: str) -> Result:
    """One console line in, lines out. Never raises; a failure is output."""
    parts = line.strip().split()
    if not parts:
        return [], None

    verb, args = parts[0].lower(), parts[1:]
    handler = _HANDLERS.get(verb)
    if handler is None:
        return ['Unknown command "%s". Try help.' % verb], "error"

    try:
        return handler(engine, graph, args)
    except Exception as exc:  # noqa: BLE001 - the console reports, never crashes
        return ["%s: %s" % (type(exc).__name__, exc)], "error"


# ── Commands ────────────────────────────────────────────────────────────────


def _help(engine, graph, args) -> Result:
    return list(HELP), None


def _status(engine: Engine, graph: Optional[Graph], args) -> Result:
    steps = chain_of(graph) if graph else []
    state = engine.state
    if state == "running" and engine.paused:
        state = "running (paused)"

    lines = [
        "runtime   plexus",
        "state     %s" % state,
        "graph     %s" % (graph.name if graph else "(none)"),
        "chain     %s" % (" -> ".join(n.id for n in steps) or "(empty)"),
    ]
    if steps:
        one_way = chain_latency(graph, steps[0].id)
        lines.append(
            "budget    %d ms each way, %d ms out and back" % (one_way, one_way * 2)
        )
    # Named here as well as logged when it happens, because the log scrolls and
    # a held chain is the state you most need to be reminded of: nothing moves
    # until it is released.
    for node_id in engine.held():
        lines.append("held      %s — release it to let the chain go" % node_id)
    return lines, None


def _nodes(engine: Engine, graph: Optional[Graph], args) -> Result:
    steps = chain_of(graph) if graph else []
    if not steps:
        return ["Nothing armed."], None

    lines = []
    for node in steps:
        telemetry = engine.telemetry(node.id)
        mean = "%.1f" % telemetry.mean_ms if telemetry.mean_ms is not None else "-"
        lines.append(
            "%-22s %-6s %6d apdu %8s ms%s"
            % (
                node.id,
                telemetry.status,
                telemetry.apdus,
                mean,
                "  %d err" % telemetry.errors if telemetry.errors else "",
            )
        )
    return lines, None


def _edges(engine: Engine, graph: Optional[Graph], args) -> Result:
    if not graph or not graph.edges:
        return ["Nothing armed."], None

    lines = []
    for edge in graph.edges:
        telemetry = engine.edge_telemetry(edge.id)
        lines.append(
            "%-44s %6d apdu  %d B out  %d B in"
            % (
                "%s -> %s" % (edge.source, edge.target),
                telemetry.apdus,
                telemetry.bytes_out,
                telemetry.bytes_in,
            )
        )
    return lines, None


def _devices(engine: Engine, graph: Optional[Graph], args) -> Result:
    devices, problems = engine.discovery()
    lines = [
        "%-4s %-18s %s" % ("ok" if d.available else "gone", d.id, d.name) for d in devices
    ]
    if not devices:
        lines = ["No devices found."]
    # Each kind that is not ready says why, so a reader that is there does not
    # hide a SimTrace2 whose daemon nobody started.
    lines += ["%s: %s" % (kind, problem) for kind, problem in sorted(problems.items())]
    if not devices and not problems:
        lines.append("Plug one in.")
    return lines, None


def _apdu_from(args) -> Tuple[Optional[bytes], Optional[Result]]:
    """
    Hex arguments to one APDU, or the refusal to print instead.

    Shared by `tx` and `release` so a command APDU means the same thing
    whichever verb carries it.
    """
    text = "".join(args).replace(":", "")
    # Checked before parsing: a megabyte of hex should not become a megabyte of
    # bytes on its way to being refused.
    if len(text) > MAX_APDU_BYTES * 2:
        return None, (
            [
                "That is %d bytes; an APDU is at most %d."
                % (len(text) // 2, MAX_APDU_BYTES)
            ],
            "error",
        )
    try:
        apdu = bytes.fromhex(text)
    except ValueError:
        return None, (["Not hex: %s" % " ".join(args)], "error")
    if len(apdu) < 4:
        return None, (["An APDU is at least four bytes (CLA INS P1 P2)."], "error")
    return apdu, None


def _tx(engine: Engine, graph: Optional[Graph], args) -> Result:
    if not args:
        return ["tx takes one hex APDU, e.g. tx 80CA9F1700"], "error"

    apdu, refusal = _apdu_from(args)
    if refusal is not None:
        return refusal

    response = engine.transmit(apdu)
    return ["-> %s" % apdu.hex().upper(), "<- %s" % response.hex().upper()], None


def _release(engine: Engine, graph: Optional[Graph], args) -> Result:
    """
    `release`, `release <hex>`, `release <node>`, `release <node> <hex>`.

    The node is optional because in practice one breakpoint is holding; it is
    accepted because more than one can be, and then you have to be able to say
    which. A leading argument that names a holding node is read as the node —
    a node id and a hex APDU are told apart by that and nothing cleverer.
    """
    holding = engine.held()
    if not holding:
        return [
            "Nothing is held. A breakpoint says so in this console the moment "
            "it catches a command."
        ], "error"

    rest = list(args)
    node_id = None
    if rest and rest[0] in holding:
        node_id = rest.pop(0)

    replacement = None
    if rest:
        replacement, refusal = _apdu_from(rest)
        if refusal is not None:
            return refusal

    released = engine.release(node_id, replacement)
    if replacement is None:
        return ["%s released." % released], None
    return ["%s released as %s." % (released, replacement.hex().upper())], None


def _pause(engine: Engine, graph, args) -> Result:
    if engine.state != "running":
        return ["Nothing is running."], "error"
    engine.pause()
    return ["Paused. Devices stay bound and the card stays powered."], None


def _resume(engine: Engine, graph, args) -> Result:
    engine.resume()
    return ["Resumed."], None


def _check(engine: Engine, graph: Optional[Graph], args) -> Result:
    if graph is None:
        return ["No graph loaded."], None

    from plexus.graph import validate

    problems = validate(graph, engine.devices())
    if not problems:
        return ["Nothing to fix."], None
    return [
        "%-7s %s%s"
        % (
            "error" if p.blocking else "warning",
            "[%s] " % p.node_id if p.node_id else "",
            p.message,
        )
        for p in problems
    ], ("error" if any(p.blocking for p in problems) else None)


_HANDLERS: Dict[str, Callable[[Engine, Optional[Graph], List[str]], Result]] = {
    "help": _help,
    "status": _status,
    "nodes": _nodes,
    "edges": _edges,
    "devices": _devices,
    "tx": _tx,
    "release": _release,
    "pause": _pause,
    "resume": _resume,
    "check": _check,
}


def verbs() -> List[str]:
    return sorted(_HANDLERS)


_ = Any

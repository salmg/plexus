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

from plexus.graph import Graph, chain as chain_of, chain_latency
from plexus.runtime import Engine

Result = Tuple[List[str], Optional[str]]  # (lines, level)

HELP = [
    "status            what the runtime is doing",
    "nodes             each node, its state and its counters",
    "edges             each edge and what has crossed it",
    "devices           hardware the runtime can see",
    "tx <hex>          push one command APDU through the chain",
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
        lines.append("budget    %d ms each way, %d ms out and back" % (one_way, one_way * 2))
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
    devices = engine.devices()
    if not devices:
        return [
            "No devices. This build binds a recorded capture and nothing else;",
            "device discovery arrives with the hardware adapters.",
        ], None
    return [
        "%-4s %-18s %s" % ("ok" if d.available else "gone", d.id, d.name) for d in devices
    ], None


def _tx(engine: Engine, graph: Optional[Graph], args) -> Result:
    if not args:
        return ["tx takes one hex APDU, e.g. tx 80CA9F1700"], "error"

    text = "".join(args).replace(":", "")
    try:
        apdu = bytes.fromhex(text)
    except ValueError:
        return ["Not hex: %s" % " ".join(args)], "error"
    if len(apdu) < 4:
        return ["An APDU is at least four bytes (CLA INS P1 P2)."], "error"

    response = engine.transmit(apdu)
    return ["-> %s" % apdu.hex().upper(), "<- %s" % response.hex().upper()], None


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
    "pause": _pause,
    "resume": _resume,
    "check": _check,
}


def verbs() -> List[str]:
    return sorted(_HANDLERS)


_ = Any

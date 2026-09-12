"""
``plexus`` — read and check a graph from the command line.

Deliberately small.  Everything here works with no hardware and no atrium
checkout, because the first question about a graph file is usually "is this
thing coherent", and that should be answerable on a laptop.

    plexus check relay.plexus.yaml
    plexus show  relay.plexus.yaml
    plexus run   relay.plexus.yaml
    plexus nodes

`run` arms the graph for real: it binds what the graph names, drives the
conversation and prints every APDU that crosses. A graph made of a recorded
capture and some transforms runs anywhere; the hardware nodes say what they
need and refuse.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plexus.catalogue import CATALOGUE, CatalogueError
from plexus.graph import Graph, GraphError, chain, chain_latency, validate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plexus", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Validate a graph file")
    check.add_argument("path", type=Path)

    show = sub.add_parser("show", help="Print the chain a graph describes")
    show.add_argument("path", type=Path)

    run = sub.add_parser("run", help="Arm the graph and drive it")
    run.add_argument("path", type=Path)
    run.add_argument(
        "--quiet",
        action="store_true",
        help="Only the summary; do not print each APDU as it crosses",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Give up after this many seconds (default 120)",
    )

    serve = sub.add_parser("serve", help="Run the runtime the editor connects to")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8900)
    serve.add_argument("--reload", action="store_true", help="Restart on code changes")

    sub.add_parser("nodes", help="List the node types this build knows")

    args = parser.parse_args(argv)

    try:
        if args.command == "nodes":
            return _nodes()
        if args.command == "serve":
            return _serve(args.host, args.port, args.reload)
        graph = Graph.load(args.path)
        if args.command == "check":
            return _check(graph)
        if args.command == "show":
            return _show(graph)
        return _run(graph, quiet=args.quiet, timeout=args.timeout)
    except (GraphError, CatalogueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"error: no such file: {args.path}", file=sys.stderr)
        return 2


def _check(graph: Graph) -> int:
    problems = validate(graph)
    if not problems:
        print(f"{graph.name}: nothing to fix.")
        return 0

    for problem in problems:
        mark = "error  " if problem.level == "error" else "warning"
        where = f" [{problem.node_id}]" if problem.node_id else ""
        print(f"{mark}{where} {problem.message}")

    errors = sum(1 for p in problems if p.blocking)
    print(
        f"\n{len(problems)} problem(s), {errors} blocking.",
        file=sys.stderr if errors else sys.stdout,
    )
    return 1 if errors else 0


def _show(graph: Graph) -> int:
    steps = chain(graph)
    if not steps:
        print(f"{graph.name}: no complete chain.")
        return 1

    print(f"{graph.name}\n")
    for i, node in enumerate(steps):
        spec = CATALOGUE.get(node.type)
        cost = f"{spec.latency_ms} ms" if spec else "?"
        host = f"  @{node.host}" if node.host else ""
        branch = "└─" if i == len(steps) - 1 else "├─"
        print(f"  {branch} {node.name}  ({node.type}, {cost}){host}")

    one_way = chain_latency(graph, steps[0].id)
    print(f"\n  {one_way} ms each way, {one_way * 2} ms out and back.")
    return 0


def _serve(host: str, port: int, reload: bool) -> int:
    """Run the runtime, and print the URL to paste into the editor."""
    try:
        from plexus.api.server import serve
    except ImportError as exc:
        print(
            "error: the server needs the runtime extra.\n"
            "    pip install -e \".[runtime]\"\n"
            "(%s)" % exc,
            file=sys.stderr,
        )
        return 2

    print("Plexus runtime on ws://%s:%d/ws" % (host, port))
    print(
        "Point the editor at it:  http://localhost:5173/?runtime=ws://%s:%d/ws\n"
        % (host, port)
    )
    serve(host=host, port=port, reload=reload)
    return 0


def _run(graph: Graph, quiet: bool = False, timeout: float = 120.0) -> int:
    """Arm the graph, drive it, and report what crossed."""
    from plexus.runtime import Engine, EngineError
    from plexus.runtime.events import Sink

    class Printing(Sink):
        """Prints APDUs as they happen rather than collecting them for later."""

        def emit(self, frame):
            if frame["type"] == "log":
                line = frame["line"]
                stream = sys.stderr if line["level"] in ("warn", "error") else sys.stdout
                print("%-5s %s" % (line["level"], line["text"]), file=stream)
            elif frame["type"] == "apdu" and not quiet:
                event = frame["event"]
                arrow = "->" if event["dir"] == "command" else "<-"
                tail = ""
                if event.get("ms") is not None:
                    tail += "  %.1f ms" % event["ms"]
                if event.get("mutatedBy"):
                    tail += "  rewritten by %s" % event["mutatedBy"]
                print("%8.1f %s %s%s" % (event["t"], arrow, event["hex"], tail))

    engine = Engine(sink=Printing())
    try:
        engine.arm(graph)
    except EngineError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    try:
        finished = engine.wait(timeout=timeout)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        finished = False
    finally:
        engine.stop()

    if not finished:
        print("error: the run did not finish within %.0fs" % timeout, file=sys.stderr)
        return 1

    exchanges = engine.telemetry(graph.nodes[0].id).apdus if graph.nodes else 0
    errors = sum(t.errors for t in engine._telemetry.values())
    print("\n%d exchanges, %d errors." % (exchanges, errors))
    return 1 if errors or engine.state == "faulted" else 0


#: "requester" and "responder" share a first letter, so the short form has to
#: say what the role does rather than abbreviate what it is called.
_ROLE = {"requester": "asks", "responder": "answers"}


def _nodes() -> int:
    from plexus.runtime import node_implementations

    ready = node_implementations()
    for node_type in sorted(CATALOGUE, key=lambda t: (t.node_class, t.key)):
        # An out-of-band port's own label already says so; no second marker.
        ports = " ".join(
            "%s(%s)" % (p.label, _ROLE.get(p.role, p.role)) for p in node_type.ports
        )
        mark = "  " if ready.get(node_type.key) == "ready" else " ·"
        print(
            f"{mark} {node_type.key:<18} {node_type.node_class:<10} "
            f"{ports:<34} {node_type.title}"
        )
    print(
        "\n· needs hardware or a daemon this build cannot start; "
        "the rest run anywhere."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

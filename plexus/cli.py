"""
``plexus`` — read and check a graph from the command line.

Deliberately small.  Everything here works with no hardware and no atrium
checkout, because the first question about a graph file is usually "is this
thing coherent", and that should be answerable on a laptop.

    plexus check relay.plexus.yaml
    plexus show  relay.plexus.yaml
    plexus nodes

Arming a graph is not here yet — that needs the runtime, and the runtime needs
a rig.  See README.md § What is not built yet.
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

    sub.add_parser("nodes", help="List the node types this build knows")

    args = parser.parse_args(argv)

    try:
        if args.command == "nodes":
            return _nodes()
        graph = Graph.load(args.path)
        return _check(graph) if args.command == "check" else _show(graph)
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
        print(f"  {'└─' if i == len(steps) - 1 else '├─'} {node.name}  ({node.type}, {cost}){host}")

    one_way = chain_latency(graph, steps[0].id)
    print(f"\n  {one_way} ms each way, {one_way * 2} ms out and back.")
    return 0


#: "requester" and "responder" share a first letter, so the short form has to
#: say what the role does rather than abbreviate what it is called.
_ROLE = {"requester": "asks", "responder": "answers"}


def _nodes() -> int:
    for node_type in sorted(CATALOGUE, key=lambda t: (t.node_class, t.key)):
        # An out-of-band port's own label already says so; no second marker.
        ports = " ".join(
            "%s(%s)" % (p.label, _ROLE.get(p.role, p.role)) for p in node_type.ports
        )
        print(
            f"{node_type.key:<18} {node_type.node_class:<10} "
            f"{ports:<34} {node_type.title}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

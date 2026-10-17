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
import shutil
import subprocess
import sys
import threading
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

    up = sub.add_parser(
        "up", help="Serve the editor and the runtime together, and open it"
    )
    up.add_argument("--host", default="127.0.0.1")
    up.add_argument("--port", type=int, default=8900)
    up.add_argument(
        "--no-open", action="store_true", help="Do not open a browser"
    )
    up.add_argument(
        "--no-build",
        action="store_true",
        help="Never run npm, even when the editor has not been built yet",
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
        if args.command == "up":
            return _up(args.host, args.port, not args.no_open, not args.no_build)
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


def _editor_source() -> Path | None:
    """
    The editor's source, if this copy has any.

    A clone has `ui/` next to the package; an install from a wheel does not, and
    the wheel either carried the built editor or it did not. Both places are
    asked rather than assumed, for the same reason atrium's are: the layout that
    holds in a clone is not the layout anyone else gets.
    """
    for base in (Path(__file__).resolve().parent.parent, Path.cwd()):
        if (base / "ui" / "package.json").is_file():
            return base / "ui"
    return None


def _build_editor(into: Path) -> bool:
    """
    Build the editor once, into the package that will serve it.

    This command used to end at a paragraph telling you to run two npm commands
    and try again, which is a strange thing for a program to do when it could
    run them. It says what it is about to run, because `npm install` reaches the
    network and nobody should learn that afterwards; `--no-build` is there for a
    bench where that is not wanted.

    Built into `into` rather than into the clone: with a non-editable install the
    package doing the serving is in site-packages, and a bundle written beside
    the source would be a build that appeared to work and changed nothing.
    """
    ui = _editor_source()
    npm = shutil.which("npm")
    if ui is None or npm is None:
        return False

    steps = []
    if not (ui / "node_modules").is_dir():
        steps.append([npm, "install"])

    # `build:bundle` writes to `../plexus/web`, which is already `into` in a
    # clone installed editable. Anywhere else the destination is appended, and
    # the later `--outDir` is the one vite uses.
    build = [npm, "run", "build:bundle"]
    if (ui.parent / "plexus" / "web").resolve() != into.resolve():
        build += ["--", "--outDir", str(into)]
    steps.append(build)

    print("The editor has not been built here yet. Building it once:")
    for step in steps:
        print("    npm %s" % " ".join(step[1:]))
    print()

    for step in steps:
        if subprocess.run(step, cwd=str(ui)).returncode != 0:
            print(file=sys.stderr)
            print(
                "That build failed. Run it yourself in ui/ to see why.",
                file=sys.stderr,
            )
            return False

    print()
    return True


def _up(host: str, port: int, open_browser: bool, build: bool = True) -> int:
    """
    One command: the editor and the runtime, on one origin.

    Everything that made this awkward was a consequence of them being two
    origins — CORS, mixed content, an allow-list to configure, and a URL with
    `?runtime=` in it to paste. Served together there is nothing to configure
    and nothing to paste, and the editor finds the runtime by asking the origin
    it was loaded from.
    """
    import webbrowser

    from plexus.api.server import WEB_ROOT, editor_is_bundled, serve

    where = "http://%s:%d/" % (host, port)

    if not editor_is_bundled() and build:
        _build_editor(WEB_ROOT)

    if not editor_is_bundled():
        print("No editor to serve.", file=sys.stderr)
        print(file=sys.stderr)
        if _editor_source() is None:
            print(
                "This copy has no ui/ to build from. Clone the repository to get"
                " one:",
                file=sys.stderr,
            )
            print(
                "    git clone --recurse-submodules"
                " https://github.com/salmg/plexus",
                file=sys.stderr,
            )
        else:
            print("Build it once, then this command serves everything:", file=sys.stderr)
            print("    cd ui && npm install && npm run build:bundle", file=sys.stderr)
        print(file=sys.stderr)
        print(
            "Or keep the runtime here and use an editor you already have:",
            file=sys.stderr,
        )
        print("    plexus serve", file=sys.stderr)
        print(
            "    then open  http://localhost:5173/?runtime=ws://%s:%d/ws"
            % (host, port),
            file=sys.stderr,
        )
        return 2

    print("Plexus on %s" % where)
    print("The editor and the runtime are both here. Ctrl-C to stop.")
    if open_browser:
        # After the server is listening, not before — a browser that arrives
        # first gets a connection refused and a blank page, and the person
        # reloads not knowing whether it worked.
        threading.Timer(1.0, lambda: webbrowser.open(where)).start()

    serve(host=host, port=port)
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
                    # An injection is not a rewrite: the same field carries
                    # "which node is responsible", and the note says which
                    # kind of responsible it is.
                    verb = (
                        "injected by"
                        if event.get("note") == "out of band"
                        else "rewritten by"
                    )
                    tail += "  %s %s" % (verb, event["mutatedBy"])
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

    # The chain head, not `graph.nodes[0]`. Those are the same node only when
    # the terminal happens to be written first in the file, and nothing makes
    # it — a graph saved with the card at the top reported the card's count,
    # or zero for a node that never ran.
    steps = chain(graph)
    exchanges = engine.telemetry(steps[0].id).apdus if steps else 0
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
    # The dots that are about this machine rather than this build, so the ones
    # worth a sentence: which piece of the stack is missing.
    from plexus.runtime import discover_hardware

    _devices, problems = discover_hardware()
    for kind, problem in sorted(problems.items()):
        if ready.get(kind) != "ready":
            print("\n%s: %s" % (kind, problem))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

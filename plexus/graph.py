"""
The Plexus graph model, Python side.

This mirrors ``ui/src/graph/types.ts`` and ``ui/src/graph/validate.ts``.  The
editor and the runtime have to agree about what a graph means, and the cheapest
way to keep them agreeing is for both to read the same catalogue (see
``plexus.catalogue``) and apply the same rules.

An edge is a conversation, not a push.  A command APDU travels from the
requester to the responder and the response comes back along the same edge, so
ports hold roles rather than directions:

    requester  — I ask.  Something on the other end has to answer.
    responder  — I answer.  I present myself as the thing being asked.

On the ``apdu`` channel those are the card port and the terminal port.  An edge
is legal when two ports share a channel and hold opposite roles, and it always
runs requester → responder.

Nothing here touches hardware or imports atrium; it is pure model code so it
can be tested on a machine with no reader in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

import yaml

from plexus.catalogue import CATALOGUE, NodeType, Port

ProblemLevel = Literal["error", "warning"]
#: A rule of thumb, not a figure from a specification. Contactless kernels
#: enforce a per-exchange timing budget; the exact number varies by kernel and
#: is not something this tool can know. This exists so a graph that is
#: obviously too slow says so before hardware is plugged in.
#:
#: Compared against the *round trip* — a command out and a response back —
#: because that is the interval a terminal actually times. The first version
#: compared the one-way sum against 120 ms, which no graph anyone could draw
#: ever reached: the dearest chain the catalogue allows is 43 ms one way, so
#: the check could not fire and was worse than no check at all.
#:
#: **One threshold, not two.** There was a second at 150 ms that raised an
#: error and blocked Run, and it had the same defect one layer down: the
#: dearest graph anyone can draw is 86 ms round trip (every transform in the
#: catalogue, on the dearest terminal and the dearest card), so reaching 150
#: took thirty-four stacked mutation nodes. Lowering it does not help either —
#: `examples/contactless-relay.plexus.yaml`, the rig this project opens with,
#: sits at 84 ms, so any reachable error threshold either fires on the flagship
#: example or lives in the two-millisecond gap above it. A warning that fires
#: for every real relay is the honest signal; a blocking check that cannot fire
#: is the thing this file keeps having to be rescued from.
#:
#: Known simplification: an injection node is counted at its own small cost,
#: not at the extra round trip it really spends reaching the card out of band.
#:
#: Keep this in step with `ui/src/graph/validate.ts`.
TAP_BUDGET_WARN_MS = 60

CONTACTLESS_TERMINALS = {"acr122u.target"}

_REF = re.compile(r"^(?P<node>.+):(?P<port>[^:]+)$")


class GraphError(ValueError):
    """A graph file that cannot be read.  User-facing."""


@dataclass(frozen=True)
class PortRef:
    node: str
    port: str

    @classmethod
    def parse(cls, text: str, where: str) -> "PortRef":
        match = _REF.match(text or "")
        if not match:
            raise GraphError(f'{where} should read "node:port", got "{text}".')
        return cls(match.group("node"), match.group("port"))

    def __str__(self) -> str:
        return f"{self.node}:{self.port}"


@dataclass
class Node:
    id: str
    type: str
    position: tuple[int, int] = (0, 0)
    config: dict[str, Any] = field(default_factory=dict)
    label: str | None = None
    host: str | None = None
    disabled: bool = False

    @property
    def spec(self) -> NodeType:
        return CATALOGUE.require(self.type)

    @property
    def name(self) -> str:
        """What to call this node in a message to a person."""
        return (self.label or "").strip() or self.spec.title


@dataclass
class Edge:
    id: str
    source: PortRef
    target: PortRef


@dataclass
class Host:
    id: str
    name: str
    address: str | None = None
    note: str | None = None


@dataclass
class Graph:
    name: str = "untitled"
    hosts: list[Host] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    version: int = 1

    # ── Lookup ──────────────────────────────────────────────────────────

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    @property
    def active(self) -> list[Node]:
        return [n for n in self.nodes if not n.disabled]

    def touching(self, ref: PortRef) -> Iterator[Edge]:
        for edge in self.edges:
            if edge.source == ref or edge.target == ref:
                yield edge

    # ── Reading and writing ─────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_yaml(cls, text: str) -> "Graph":
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise GraphError(f"Not valid YAML: {exc}") from exc

        if not isinstance(raw, dict):
            raise GraphError("The file is empty.")
        if raw.get("version") != 1:
            raise GraphError(
                f"Unsupported graph version {raw.get('version')!r}. "
                "This build reads version 1."
            )

        # Field for field, this mirrors `parseGraph` in
        # ui/src/graph/serialize.ts — including where that is lenient. A
        # divergence means one file is two graphs depending on which half read
        # it, and the editor is the half whose rules are already written down,
        # so these follow it rather than improve on it.
        #
        # `nodes` missing is an error there. It used to load as an empty graph
        # here, so a truncated file was "valid, just empty" to `plexus check`.
        raw_nodes = raw.get("nodes")
        if not isinstance(raw_nodes, list):
            raise GraphError("`nodes` must be a list.")
        nodes = [_node_from(entry, i) for i, entry in enumerate(raw_nodes)]

        seen: set[str] = set()
        for node in nodes:
            if node.id in seen:
                raise GraphError(f'Two nodes share the id "{node.id}".')
            seen.add(node.id)

        # Absent means none; anything else that is not a list is an error.
        raw_edges = raw.get("edges")
        if raw_edges is None:
            raw_edges = []
        if not isinstance(raw_edges, list):
            raise GraphError("`edges` must be a list.")
        edges = [_edge_from(entry, i, seen) for i, entry in enumerate(raw_edges)]

        # `hosts` is the one field the editor shrugs at: a non-list is ignored,
        # and entries that are not mappings are dropped rather than refused.
        raw_hosts = raw.get("hosts")
        host_entries = [
            h
            for h in (raw_hosts if isinstance(raw_hosts, list) else [])
            if isinstance(h, dict)
        ]

        return cls(
            name=_opt_str(raw.get("name")) or "untitled",
            hosts=[_host_from(h, i) for i, h in enumerate(host_entries)],
            nodes=nodes,
            edges=edges,
        )

    def to_yaml(self) -> str:
        document: dict[str, Any] = {"version": 1, "name": self.name}
        if self.hosts:
            document["hosts"] = [
                {k: v for k, v in vars(h).items() if v is not None} for h in self.hosts
            ]
        document["nodes"] = [_node_to(n) for n in self.nodes]
        document["edges"] = [
            {"id": e.id, "from": str(e.source), "to": str(e.target)} for e in self.edges
        ]
        return yaml.safe_dump(document, sort_keys=False, width=88, allow_unicode=True)


# ── Problems ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Problem:
    code: str
    level: ProblemLevel
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    @property
    def blocking(self) -> bool:
        return self.level == "error"


@dataclass(frozen=True)
class Device:
    id: str
    kind: str
    name: str
    host: str = ""
    available: bool = True
    detail: str | None = None


def validate(graph: Graph, devices: Iterable[Device] = ()) -> list[Problem]:
    """Every reason this graph should not be armed, worst first."""
    by_id = {d.id: d for d in devices}
    problems: list[Problem] = []

    if not graph.active:
        return [
            Problem("empty", "warning", "The graph is empty."),
        ]

    problems += _edge_problems(graph)
    problems += _chain_problems(graph)
    problems += _port_problems(graph)
    problems += _device_problems(graph, by_id)
    problems += _host_problems(graph)
    problems += _timing_problems(graph)

    # Is there a chain at all? Last, and only when nothing above already
    # explains why not — "the probe has nothing on its card port" is worth more
    # than "no complete chain", and saying both says the second one twice.
    #
    # The engine has always refused a graph with no chain at `arm`, with exactly
    # this sentence and exactly this "only if nothing else blocked" condition.
    # It was the one rule it kept to itself, so a graph with nothing at its head
    # — or whose only target is parked — passed `plexus check` with "0 blocking",
    # left Run enabled in the editor, and faulted a moment later. Measured: a
    # probe wired to a disabled capture reported *no problems at all*.
    if not any(p.blocking for p in problems) and len(chain(graph)) < 2:
        problems.append(
            Problem(
                "no-chain",
                "error",
                "No complete chain: a graph needs something that asks and "
                "something that answers.",
            )
        )

    problems.sort(key=lambda p: 0 if p.level == "error" else 1)
    return problems


def _edge_problems(graph: Graph) -> list[Problem]:
    """
    Is every edge one the editor would have let you draw?

    The editor refuses these while the wire is still being dragged, in
    ``canConnect``. Nothing here re-derived them, so a graph that never went
    through the editor — hand-written, generated, or sent by someone else —
    could name a cycle, a self-edge, two requesters wired together or one port
    carrying two conversations, pass ``plexus check`` clean, and reach ``arm``.
    What happened then depended on which rule was broken: a doubly-wired port
    quietly lost an edge, because ``chain`` follows the first one it finds.

    Kept deliberately parallel to ``canConnect`` in
    ``ui/src/graph/validate.ts``: same conditions, same wording, so the two
    sides of the project agree about what a legal graph is.
    """
    out: list[Problem] = []
    active = {n.id for n in graph.active}
    claimed: dict[PortRef, str] = {}

    for edge in graph.edges:
        if edge.source.node not in active or edge.target.node not in active:
            # An edge onto a disabled node is not an error — disabling is how you
            # park part of a rig — and a missing node is refused at parse time.
            continue

        if edge.source.node == edge.target.node:
            out.append(
                Problem(
                    "self-edge",
                    "error",
                    "%s is wired to itself. A node cannot talk to itself."
                    % (graph.node(edge.source.node).name),
                    node_id=edge.source.node,
                    edge_id=edge.id,
                )
            )
            continue

        source = _port_of(graph, edge.source)
        target = _port_of(graph, edge.target)

        if source is None or target is None:
            missing = edge.source if source is None else edge.target
            out.append(
                Problem(
                    "unknown-port",
                    "error",
                    'No port "%s" on %s.'
                    % (missing.port, graph.node(missing.node).name),
                    node_id=missing.node,
                    edge_id=edge.id,
                )
            )
            continue

        if source.channel != target.channel:
            out.append(
                Problem(
                    "wrong-channel",
                    "error",
                    "A %s port does not carry %s." % (source.channel, target.channel),
                    node_id=edge.source.node,
                    edge_id=edge.id,
                )
            )
            continue

        if source.role == target.role:
            both = "ask" if source.role == "requester" else "answer"
            out.append(
                Problem(
                    "same-role",
                    "error",
                    "Both ports %s. An edge joins one that asks to one that answers."
                    % both,
                    node_id=edge.source.node,
                    edge_id=edge.id,
                )
            )
            continue

        # One conversation per port. Fanning a card out to two terminals is not a
        # graph this can run — it is two graphs.
        #
        # The dashed line is the exception, and it has to be: an out-of-band
        # edge lands on the terminal port of a card that is already in a chain,
        # by definition — that is what "past everything downstream" means. With
        # the rule applied to both ends, an inject node could not be wired at
        # all. Draw the chain and the dashed edge is refused; draw the dashed
        # edge and the chain is refused.
        #
        # So the source is counted — one dashed line per node, since that port
        # carries one conversation like any other — and the target is not.
        ends = [(edge.source, source)]
        if not source.oob:
            ends.append((edge.target, target))

        for ref, port in ends:
            held = claimed.get(ref)
            if held is not None and held != edge.id:
                out.append(
                    Problem(
                        "port-contended",
                        "error",
                        "The %s port on %s is wired twice."
                        % (port.label, graph.node(ref.node).name),
                        node_id=ref.node,
                        edge_id=edge.id,
                    )
                )
            else:
                claimed[ref] = edge.id

    cycle = _find_cycle(graph)
    if cycle:
        out.append(
            Problem(
                "cycle",
                "error",
                "These nodes form a loop: %s. A conversation has to end somewhere."
                % " -> ".join(cycle),
                node_id=cycle[0],
            )
        )

    return out


def _port_of(graph: Graph, ref: PortRef) -> Port | None:
    node = graph.node(ref.node)
    if node is None:
        return None
    spec = CATALOGUE.get(node.type)
    if spec is None:
        return None
    return next((p for p in spec.ports if p.id == ref.port), None)


def _find_cycle(graph: Graph) -> list[str]:
    """
    The first cycle reachable by following edges, as a node-id path.

    Out-of-band edges are followed too: an injection wired back into its own
    upstream is still a loop, and it would still hang.
    """
    following: dict[str, list[str]] = {}
    for edge in graph.edges:
        following.setdefault(edge.source.node, []).append(edge.target.node)

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}

    def walk(node_id: str, path: list[str]) -> list[str]:
        colour[node_id] = GREY
        path.append(node_id)
        for nxt in following.get(node_id, ()):
            shade = colour.get(nxt, WHITE)
            if shade == GREY:
                return path[path.index(nxt) :] + [nxt]
            if shade == WHITE:
                found = walk(nxt, path)
                if found:
                    return found
        path.pop()
        colour[node_id] = BLACK
        return []

    for node in graph.active:
        if colour.get(node.id, WHITE) == WHITE:
            found = walk(node.id, [])
            if found:
                return found
    return []


def _port_problems(graph: Graph) -> list[Problem]:
    out: list[Problem] = []
    for node in graph.active:
        try:
            spec = node.spec
        except KeyError:
            out.append(
                Problem(
                    "unknown-type",
                    "error",
                    f'Unknown node type "{node.type}". '
                    "This graph was saved by a different version.",
                    node_id=node.id,
                )
            )
            continue

        for port in spec.ports:
            if any(graph.touching(PortRef(node.id, port.id))):
                continue
            # A requester with nothing to ask cannot run.  A responder with
            # nobody asking is merely unused, which is fine mid-build.
            if port.role == "requester" and not port.oob:
                out.append(
                    Problem(
                        "unwired-requester",
                        "error",
                        f"{node.name} has nothing on its {port.label} port.",
                        node_id=node.id,
                    )
                )
            elif port.role == "requester":
                # An out-of-band port with nothing on it is not an error — the
                # node still passes traffic through — but the one thing it is
                # for cannot happen. It used to inject anyway, because the
                # engine handed every transform a line to the card whether one
                # was drawn or not; now the edge is what authorises it, so
                # saying so before Run is the difference between a node that
                # does nothing and a node that does nothing silently.
                out.append(
                    Problem(
                        "unwired-oob",
                        "warning",
                        f"{node.name} has nothing on its {port.label} port, "
                        "so it will pass traffic through and inject nothing.",
                        node_id=node.id,
                    )
                )
            elif port.role == "responder":
                out.append(
                    Problem(
                        "unwired-responder",
                        "warning",
                        f"Nothing is asking {node.name} anything. It will sit idle.",
                        node_id=node.id,
                    )
                )
    return out


def _device_problems(graph: Graph, devices: dict[str, Device]) -> list[Problem]:
    out: list[Problem] = []
    claims: dict[str, list[Node]] = {}

    for node in graph.active:
        spec = CATALOGUE.get(node.type)
        if spec is None or spec.device is None:
            continue
        field_key = next((f.key for f in spec.fields if f.kind == "device"), None)
        if field_key is None:
            continue
        bound = node.config.get(field_key)
        if not isinstance(bound, str) or not bound:
            out.append(
                Problem(
                    "unbound-device",
                    "error",
                    f"{node.name} has no device selected.",
                    node_id=node.id,
                )
            )
            continue
        claims.setdefault(bound, []).append(node)

    for device_id, holders in claims.items():
        device = devices.get(device_id)
        label = device.name if device else device_id

        if device is not None and not device.available:
            out.append(
                Problem(
                    "device-missing",
                    "error",
                    f"{label} is not present. Plug it in, or pick another.",
                    node_id=holders[0].id,
                )
            )

        if len(holders) > 1:
            names = " and ".join(n.name for n in holders)
            out.append(
                Problem(
                    "device-contended",
                    "error",
                    f"{names} both claim {label}. One device cannot be a target "
                    "and an initiator at the same time — the failure that comes "
                    "out of trying is a timeout, not a message.",
                    node_id=holders[1].id,
                )
            )
    return out


def _host_problems(graph: Graph) -> list[Problem]:
    out: list[Problem] = []
    host_of = {n.id: (n.host or "") for n in graph.active}

    for edge in graph.edges:
        source_host = host_of.get(edge.source.node)
        target_host = host_of.get(edge.target.node)
        if source_host is None or target_host is None or source_host == target_host:
            continue

        target = graph.node(edge.target.node)
        if target is None or CATALOGUE.get(target.type) is None:
            continue

        protected = any(
            isinstance(target.config.get(key), str) and target.config[key].strip()
            for key in ("pairing", "cafile")
        )
        if not protected:
            out.append(
                Problem(
                    "unpaired-link",
                    "warning",
                    "This edge crosses machines and carries card traffic "
                    f"unauthenticated. Set a pairing secret on {target.name}, "
                    "or tunnel it over SSH.",
                    node_id=target.id,
                    edge_id=edge.id,
                )
            )
    return out


def chain_latency(graph: Graph, start_id: str) -> int:
    """
    Expected milliseconds per exchange along the chain starting at a node.

    Active nodes only, for the same reason `chain` walks active nodes only: a
    disabled node is not in the conversation, and counting its latency made the
    tap-budget check answer about a rig that is not the one being run.
    """
    total = 0
    seen: set[str] = set()
    alive = {n.id: n for n in graph.active}
    current: str | None = start_id

    while current and current not in seen:
        seen.add(current)
        node = alive.get(current)
        if node is None:
            break
        spec = CATALOGUE.get(node.type)
        total += spec.latency_ms if spec else 0
        step = next(
            (
                e
                for e in graph.edges
                if e.source.node == current and e.source.port != "oob"
            ),
            None,
        )
        current = step.target.node if step else None

    return total


def _timing_problems(graph: Graph) -> list[Problem]:
    out: list[Problem] = []
    for node in graph.active:
        if node.type not in CONTACTLESS_TERMINALS:
            continue
        # Out and back: the interval the terminal times, not the one-way sum.
        ms = chain_latency(graph, node.id) * 2
        if ms >= TAP_BUDGET_WARN_MS:
            out.append(
                Problem(
                    "tap-budget",
                    "warning",
                    f"About {ms} ms per exchange, out and back. Close to what a "
                    "contactless kernel will tolerate — expect dropped taps.",
                    node_id=node.id,
                )
            )
    return out


def _chain_problems(graph: Graph) -> list[Problem]:
    """
    One graph, one conversation.

    `chain` starts at a node that only asks and follows the edges. When there
    is more than one such node there is more than one chain, and it takes
    `roots[0]` — so a graph with two complete rigs in it armed, ran one, and
    said nothing about the other. Which one depended on the order the nodes
    happened to be written in.

    `ui/src/runtime/mock.ts` takes `roots[0]` too, so both halves ran the same
    half of the graph and neither mentioned it.
    """
    roots = [
        n
        for n in graph.active
        if (spec := CATALOGUE.get(n.type)) is not None
        and any(p.role == "requester" and not p.oob for p in spec.ports)
        and not any(p.role == "responder" for p in spec.ports)
    ]
    if len(roots) < 2:
        return []

    # Named *and* identified. Two chains often start at two nodes of the same
    # type, and "Fingerprint probe, Fingerprint probe" says nothing about which
    # one to remove.
    def where(node: Node) -> str:
        return "%s (%s)" % (node.name, node.id)

    return [
        Problem(
            "two-chains",
            "error",
            "This graph has %d separate chains: %s. Plexus runs one at a time, "
            "and would run the one starting at %s."
            % (len(roots), ", ".join(where(n) for n in roots), where(roots[0])),
            node_id=roots[1].id,
        )
    ]


def chain(graph: Graph) -> list[Node]:
    """
    The nodes a command crosses, in order.

    Starts at a node that only asks, follows the edge leaving each one, and
    stops when nothing asks further.  Out-of-band edges are skipped: they are a
    side channel to the card rather than a step in the conversation, and
    counting them would double the latency of any graph with an injection node.

    Disabled nodes end the walk.  Disabling is how you park part of a rig, so
    the conversation stops where the parked node is, and `arm` then refuses a
    chain with nothing answering at the end of it.  This used to advance with
    `graph.node()`, which searches every node — so the engine bound and drove a
    node the editor greys out and skips, and the two halves disagreed about what
    was running.  `ui/src/runtime/mock.ts` truncates here too.
    """
    roots = [
        n
        for n in graph.active
        if (spec := CATALOGUE.get(n.type)) is not None
        and any(p.role == "requester" and not p.oob for p in spec.ports)
        and not any(p.role == "responder" for p in spec.ports)
    ]
    if not roots:
        return []

    out: list[Node] = []
    seen: set[str] = set()
    alive = {n.id: n for n in graph.active}
    current: Node | None = roots[0]

    while current is not None and current.id not in seen:
        seen.add(current.id)
        out.append(current)
        step = next(
            (
                e
                for e in graph.edges
                if e.source.node == current.id and e.source.port != "oob"
            ),
            None,
        )
        current = alive.get(step.target.node) if step else None

    return out


# ── Parsing helpers ─────────────────────────────────────────────────────────


def _opt_str(value: Any) -> str | None:
    """
    The value if it is a string, otherwise nothing.

    `typeof x === "string" ? x : undefined`, which is what the editor does with
    every optional text field. Taking these as-is is how a non-string `label`
    reached `Node.name`, where `.strip()` raised AttributeError — a crash on
    the Python side for a file the editor had quietly accepted.
    """
    return value if isinstance(value, str) else None


def _position(at: Any) -> tuple[int, int]:
    """
    Two numbers, or the origin.

    `int(at[0])` accepted the string "40" and raised ValueError on "x" — and a
    ValueError is not a GraphError, so over a socket it tore the session down
    instead of answering with an error frame. The editor requires numbers, so
    this requires numbers. Booleans are excluded because `isinstance(True,
    int)` is true in Python and `typeof true === "number"` is false in
    JavaScript, and this file exists to agree with that one.
    """
    if (
        isinstance(at, (list, tuple))
        and len(at) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in at)
    ):
        return (int(at[0]), int(at[1]))
    return (0, 0)


def _node_from(entry: Any, index: int) -> Node:
    if not isinstance(entry, dict):
        raise GraphError(f"Node {index} is not a mapping.")

    node_id = _require_str(entry.get("id"), f"nodes[{index}].id")
    type_key = _require_str(entry.get("type"), f"nodes[{index}].type")
    if CATALOGUE.get(type_key) is None:
        raise GraphError(
            f'Node "{node_id}" has type "{type_key}", which this build does not know.'
        )

    config = entry.get("config")

    return Node(
        id=node_id,
        type=type_key,
        position=_position(entry.get("at")),
        config=config if isinstance(config, dict) else {},
        label=_opt_str(entry.get("label")),
        host=_opt_str(entry.get("host")),
        # `is True`, not `bool(...)`: the editor's rule is `=== true`, so
        # `disabled: 1` parked a node here and left it running there.
        disabled=entry.get("disabled") is True,
    )


def _edge_from(entry: Any, index: int, known: set[str]) -> Edge:
    if not isinstance(entry, dict):
        raise GraphError(f"Edge {index} is not a mapping.")

    source = PortRef.parse(
        _require_str(entry.get("from"), f"edges[{index}].from"), f"edges[{index}].from"
    )
    target = PortRef.parse(
        _require_str(entry.get("to"), f"edges[{index}].to"), f"edges[{index}].to"
    )

    for ref, where in ((source, "from"), (target, "to")):
        if ref.node not in known:
            raise GraphError(
                f'edges[{index}].{where} names node "{ref.node}", which is not defined.'
            )

    # A non-string id was stringified here and ignored there, so `id: 5` gave
    # one graph two different edge ids depending on who read it.
    return Edge(
        id=_opt_str(entry.get("id")) or f"{source}->{target}",
        source=source,
        target=target,
    )


def _host_from(entry: Any, index: int) -> Host:
    if not isinstance(entry, dict):
        raise GraphError(f"Host {index} is not a mapping.")
    host_id = _require_str(entry.get("id"), f"hosts[{index}].id")
    name = entry.get("name")
    return Host(
        id=host_id,
        name=name if isinstance(name, str) else host_id,
        address=_opt_str(entry.get("address")),
        note=_opt_str(entry.get("note")),
    )

def _node_to(node: Node) -> dict[str, Any]:
    out: dict[str, Any] = {"id": node.id, "type": node.type}
    if node.label:
        out["label"] = node.label
    if node.host:
        out["host"] = node.host
    if node.disabled:
        out["disabled"] = True
    out["at"] = [int(node.position[0]), int(node.position[1])]
    if node.config:
        out["config"] = node.config
    return out


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise GraphError(f"{where} must be a non-empty string.")
    return value

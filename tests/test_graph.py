"""
The graph model and its checks.

These matter more than they look. The editor runs the same rules in TypeScript,
and the two implementations agreeing is the whole reason a graph can be drawn
on a laptop and armed on a rig. Where a case pins a number the editor also
produces — the 42 ms budget of the example rig, say — it is pinning that
agreement, not the arithmetic.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from plexus.catalogue import CATALOGUE
from plexus.graph import (
    TAP_BUDGET_FAIL_MS,
    TAP_BUDGET_WARN_MS,
    Device,
    Graph,
    GraphError,
    PortRef,
    chain,
    chain_latency,
    validate,
)

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "contactless-relay.plexus.yaml"
)


def build(body: str) -> Graph:
    return Graph.from_yaml(textwrap.dedent(body))


MINIMAL = """
    version: 1
    name: minimal
    nodes:
      - id: t
        type: acr122u.target
        at: [0, 0]
        config: {device: acr122u:001}
      - id: c
        type: capture
        at: [200, 0]
        config: {path: logs/x.jsonl}
    edges:
      - from: t:card
        to: c:terminal
"""


# ── Reading ─────────────────────────────────────────────────────────────────


def test_reads_the_example_rig():
    graph = Graph.load(EXAMPLE)
    assert graph.name == "contactless relay to a phone"
    assert [n.id for n in graph.nodes] == [
        "acr122u-target-1",
        "trace-1",
        "mutation-1",
        "nfcgate-1",
    ]
    assert len(graph.edges) == 3
    assert graph.edges[0].source == PortRef("acr122u-target-1", "card")
    assert graph.edges[0].target == PortRef("trace-1", "terminal")


def test_a_playbook_survives_the_round_trip():
    """A mutation node's config is an atrium playbook; it must come back whole."""
    graph = Graph.load(EXAMPLE)
    playbook = graph.node("mutation-1").config["playbook"]
    assert "9F02" in playbook

    again = Graph.from_yaml(graph.to_yaml())
    assert again.node("mutation-1").config["playbook"] == playbook
    assert [n.id for n in again.nodes] == [n.id for n in graph.nodes]
    assert [(str(e.source), str(e.target)) for e in again.edges] == [
        (str(e.source), str(e.target)) for e in graph.edges
    ]


def test_label_and_host_survive_the_round_trip():
    graph = Graph.load(EXAMPLE)
    again = Graph.from_yaml(graph.to_yaml())
    assert again.node("mutation-1").label == "CVM bypass"
    assert again.node("nfcgate-1").host == "phone"
    assert again.node("acr122u-target-1").position == (40, 160)


# ── Refusing to read ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body, expected",
    [
        ("version: 2\nname: x\nnodes: []\n", "version"),
        ("", "empty"),
        ("version: 1\nname: x\nnodes:\n  - id: a\n    type: nope\n", "does not know"),
        (
            "version: 1\nname: x\nnodes:\n"
            "  - id: a\n    type: capture\n  - id: a\n    type: capture\n",
            "share the id",
        ),
        (
            "version: 1\nname: x\nnodes:\n  - id: a\n    type: capture\n"
            "edges:\n  - from: a\n    to: a:terminal\n",
            "node:port",
        ),
        (
            "version: 1\nname: x\nnodes:\n  - id: a\n    type: capture\n"
            "edges:\n  - from: ghost:card\n    to: a:terminal\n",
            "not defined",
        ),
    ],
)
def test_a_broken_file_is_refused_with_a_reason(body, expected):
    with pytest.raises(GraphError) as caught:
        Graph.from_yaml(body)
    assert expected in str(caught.value)


# ── Checks ──────────────────────────────────────────────────────────────────


def test_the_example_rig_warns_twice_and_blocks_nothing():
    """
    Both warnings are true of relaying a tap to a phone: the link is
    unauthenticated, and 84 ms out and back is marginal for a contactless
    kernel. Neither should stop you arming it and finding out.
    """
    problems = validate(Graph.load(EXAMPLE))
    assert sorted(p.code for p in problems) == ["tap-budget", "unpaired-link"]
    assert not any(p.blocking for p in problems)


def test_a_pairing_secret_settles_the_cross_machine_warning():
    graph = Graph.load(EXAMPLE)
    graph.node("nfcgate-1").config["cafile"] = "/etc/plexus/rig-ca.pem"
    # The timing warning is about the phone, not the certificate, and stays.
    assert [p.code for p in validate(graph)] == ["tap-budget"]


def test_a_requester_with_nothing_to_ask_cannot_run():
    graph = build(MINIMAL)
    graph.edges.clear()
    codes = [p.code for p in validate(graph)]
    assert "unwired-requester" in codes
    assert any(p.blocking for p in validate(graph))


def test_a_responder_nobody_asks_is_only_a_warning():
    """Half-built is a normal state; it should not read as an error."""
    graph = build(MINIMAL)
    graph.edges.clear()
    responder = next(p for p in validate(graph) if p.code == "unwired-responder")
    assert not responder.blocking


def test_one_device_cannot_be_claimed_twice():
    graph = build(
        """
        version: 1
        name: contended
        nodes:
          - id: t
            type: acr122u.target
            at: [0, 0]
            config: {device: acr122u:001}
          - id: r
            type: acr122u.reader
            at: [200, 0]
            config: {device: acr122u:001}
        edges:
          - from: t:card
            to: r:terminal
        """
    )
    problem = next(p for p in validate(graph) if p.code == "device-contended")
    assert problem.blocking
    assert "target and an initiator" in problem.message


def test_a_second_reader_settles_the_contention():
    graph = build(
        """
        version: 1
        name: two readers
        nodes:
          - id: t
            type: acr122u.target
            at: [0, 0]
            config: {device: acr122u:001}
          - id: r
            type: acr122u.reader
            at: [200, 0]
            config: {device: acr122u:002}
        edges:
          - from: t:card
            to: r:terminal
        """
    )
    assert validate(graph) == []


def test_a_missing_device_blocks():
    graph = build(MINIMAL)
    devices = [Device(id="acr122u:001", kind="acr122u", name="ACR122U", available=False)]
    problem = next(p for p in validate(graph, devices) if p.code == "device-missing")
    assert problem.blocking


def test_a_node_with_no_device_chosen_blocks():
    graph = build(MINIMAL)
    graph.node("t").config.pop("device")
    assert any(p.code == "unbound-device" and p.blocking for p in validate(graph))


# ── Timing ──────────────────────────────────────────────────────────────────


def test_the_example_rig_costs_what_the_editor_says():
    graph = Graph.load(EXAMPLE)
    assert chain_latency(graph, "acr122u-target-1") == 42


def test_relaying_a_tap_to_a_phone_warns_about_timing():
    """
    The check is measured against the round trip, and the reason it is worth
    having is exactly this rig: 42 ms each way over a phone on wifi is where
    contactless taps start being dropped.
    """
    problem = next(
        p for p in validate(Graph.load(EXAMPLE)) if p.code == "tap-budget"
    )
    assert not problem.blocking
    assert "84 ms" in problem.message
    assert "out and back" in problem.message


def test_a_local_two_reader_relay_says_nothing_about_timing():
    """A relay with no network in it has room to spare; it should stay quiet."""
    graph = build(
        """
        version: 1
        name: local
        nodes:
          - id: t
            type: acr122u.target
            at: [0, 0]
            config: {device: acr122u:001}
          - id: r
            type: acr122u.reader
            at: [200, 0]
            config: {device: acr122u:002}
        edges:
          - from: t:card
            to: r:terminal
        """
    )
    assert chain_latency(graph, "t") * 2 == 30
    assert [p.code for p in validate(graph)] == []


def test_the_thresholds_are_reachable_by_a_graph_someone_could_draw():
    """
    Regression on a check that did nothing. The first thresholds were compared
    against the one-way sum and set at 120/300 ms, while the dearest chain the
    catalogue allows is 43 ms one way — so the warning could not fire and the
    error needed 79 stacked mutation nodes.
    """
    dearest_terminal = max(
        (t for t in CATALOGUE if t.node_class == "terminal"),
        key=lambda t: t.latency_ms,
    )
    dearest_card = max(
        (t for t in CATALOGUE if t.node_class == "card"), key=lambda t: t.latency_ms
    )
    every_transform = sum(
        t.latency_ms for t in CATALOGUE if t.node_class == "transform"
    )
    worst_round_trip = (
        dearest_terminal.latency_ms + every_transform + dearest_card.latency_ms
    ) * 2

    assert worst_round_trip >= TAP_BUDGET_WARN_MS, (
        "no drawable graph can reach the warning threshold"
    )
    # The error is meant to be hard to reach, but not impossible: a couple of
    # transforms on top of the worst chain has to be able to get there.
    assert TAP_BUDGET_FAIL_MS <= worst_round_trip * 2


def test_an_out_of_band_edge_is_not_a_step_in_the_chain():
    """
    Injection reaches the card past the terminal. Counting that wire would
    double the cost of every graph with an injection node in it.
    """
    from plexus.graph import Edge

    graph = build(
        """
        version: 1
        name: injected
        nodes:
          - id: t
            type: acr122u.target
            at: [0, 0]
            config: {device: acr122u:001}
          - id: i
            type: inject
            at: [100, 0]
            config: {}
          - id: c
            type: capture
            at: [200, 0]
            config: {path: logs/x.jsonl}
        edges:
          - from: t:card
            to: i:terminal
          - from: i:card
            to: c:terminal
        """
    )
    before = chain_latency(graph, "t")
    graph.edges.append(Edge("oob", PortRef("i", "oob"), PortRef("c", "terminal")))
    assert chain_latency(graph, "t") == before
    assert [n.id for n in chain(graph)] == ["t", "i", "c"]


# ── The chain ───────────────────────────────────────────────────────────────


def test_the_chain_runs_from_the_node_that_only_asks():
    graph = Graph.load(EXAMPLE)
    assert [n.id for n in chain(graph)] == [
        "acr122u-target-1",
        "trace-1",
        "mutation-1",
        "nfcgate-1",
    ]


def test_a_disabled_node_is_not_in_the_chain():
    graph = Graph.load(EXAMPLE)
    graph.node("acr122u-target-1").disabled = True
    assert "acr122u-target-1" not in [n.id for n in chain(graph)]


def test_disabling_a_middle_node_ends_the_chain_there():
    """
    The walk used to advance with `graph.node()`, which searches every node,
    disabled included. So disabling `trace-1` — which the editor greys out and
    steps over — left this side returning all four nodes, and the engine bound
    and drove the node the editor says is skipped.

    `ui/src/runtime/mock.ts` truncates at the parked node, so this truncates.
    """
    graph = Graph.load(EXAMPLE)
    graph.node("trace-1").disabled = True
    assert [n.id for n in chain(graph)] == ["acr122u-target-1"]


def test_a_disabled_node_does_not_count_towards_the_tap_budget():
    """Latency walked into disabled nodes too, so the budget check answered
    about a rig that was not the one being run."""
    graph = Graph.load(EXAMPLE)
    whole = chain_latency(graph, "acr122u-target-1")
    graph.node("trace-1").disabled = True
    parked = chain_latency(graph, "acr122u-target-1")
    assert parked < whole
    assert parked == CATALOGUE.get("acr122u.target").latency_ms


def test_no_terminal_means_no_chain():
    graph = build(MINIMAL)
    graph.nodes = [n for n in graph.nodes if n.id != "t"]
    assert chain(graph) == []


# ── Edge legality: the rules the editor enforces while you drag a wire ──────
#
# `canConnect` in ui/src/graph/validate.ts refuses these before the wire lands.
# Nothing on this side re-derived them, so a graph that never went through the
# editor could carry any of them, pass `plexus check` clean and reach `arm`.


def test_a_self_edge_is_refused():
    graph = build(
        """
        version: 1
        name: talks to itself
        nodes:
          - id: m
            type: mutation
            at: [0, 0]
            config: {playbook: "enabled: true"}
        edges:
          - from: m:card
            to: m:terminal
        """
    )
    problem = next(p for p in validate(graph) if p.code == "self-edge")
    assert problem.blocking
    assert "cannot talk to itself" in problem.message


def test_two_ports_that_both_ask_are_refused():
    graph = build(
        """
        version: 1
        name: both ask
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: quick}
          - id: q
            type: agent
            at: [200, 0]
            config: {}
        edges:
          - from: p:card
            to: q:card
        """
    )
    problem = next(p for p in validate(graph) if p.code == "same-role")
    assert problem.blocking
    assert "Both ports ask" in problem.message


def test_two_ports_that_both_answer_are_refused():
    graph = build(
        """
        version: 1
        name: both answer
        nodes:
          - id: a
            type: capture
            at: [0, 0]
            config: {path: tests/captures/mastercard-probe.jsonl}
          - id: b
            type: capture
            at: [200, 0]
            config: {path: tests/captures/mastercard-probe.jsonl}
        edges:
          - from: a:terminal
            to: b:terminal
        """
    )
    problem = next(p for p in validate(graph) if p.code == "same-role")
    assert "Both ports answer" in problem.message


def test_an_unknown_port_is_refused():
    graph = build(
        """
        version: 1
        name: no such port
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: quick}
          - id: c
            type: capture
            at: [200, 0]
            config: {path: tests/captures/mastercard-probe.jsonl}
        edges:
          - from: p:nonsense
            to: c:terminal
        """
    )
    problem = next(p for p in validate(graph) if p.code == "unknown-port")
    assert problem.blocking
    assert "nonsense" in problem.message


def test_one_port_cannot_carry_two_conversations():
    """
    The quiet one: `chain` follows the first edge it finds, so a doubly-wired
    card port silently dropped the other branch rather than failing.
    """
    graph = build(
        """
        version: 1
        name: fanned out
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: quick}
          - id: a
            type: capture
            at: [200, 0]
            config: {path: tests/captures/mastercard-probe.jsonl}
          - id: b
            type: capture
            at: [200, 200]
            config: {path: tests/captures/mastercard-probe.jsonl}
        edges:
          - id: e1
            from: p:card
            to: a:terminal
          - id: e2
            from: p:card
            to: b:terminal
        """
    )
    problem = next(p for p in validate(graph) if p.code == "port-contended")
    assert problem.blocking
    assert "wired twice" in problem.message


def test_a_loop_is_refused():
    graph = build(
        """
        version: 1
        name: round and round
        nodes:
          - id: x
            type: trace
            at: [0, 0]
            config: {}
          - id: y
            type: mutation
            at: [200, 0]
            config: {playbook: "enabled: true"}
        edges:
          - from: x:card
            to: y:terminal
          - from: y:card
            to: x:terminal
        """
    )
    problem = next(p for p in validate(graph) if p.code == "cycle")
    assert problem.blocking
    assert "loop" in problem.message


def test_the_example_rigs_still_pass_the_new_edge_checks():
    """Whatever the new rules refuse, they must not refuse what already worked."""
    for rig in sorted((REPO / "examples").glob("*.yaml")):
        problems = validate(Graph.load(rig))
        assert not [p for p in problems if p.blocking], f"{rig.name}: {problems}"


def test_a_disabled_node_does_not_trip_the_edge_checks():
    """Disabling half a rig is how you park it; its wires are not errors."""
    graph = build(
        """
        version: 1
        name: parked
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: quick}
          - id: c
            type: capture
            at: [200, 0]
            config: {path: tests/captures/mastercard-probe.jsonl}
          - id: spare
            type: capture
            disabled: true
            at: [200, 200]
            config: {path: tests/captures/mastercard-probe.jsonl}
        edges:
          - from: p:card
            to: c:terminal
          - from: p:card
            to: spare:terminal
        """
    )
    assert not [p for p in validate(graph) if p.blocking]


# ── Reading a file: the same file, the same graph ───────────────────────────
#
# `parseGraph` in ui/src/graph/serialize.ts is the other half of this. Where
# the two disagreed, one file was two graphs depending on which half read it —
# and in three of these cases the Python half did not merely disagree, it
# raised something that is not a GraphError, which over a socket is the
# difference between an error frame and a torn-down session.


def one_node(extra: str = "") -> Graph:
    """A one-node graph, with `extra` lines added to that node."""
    return Graph.from_yaml(
        "version: 1\nname: x\nnodes:\n  - id: t\n    type: probe\n" + extra
    )


def test_a_file_with_no_nodes_key_is_refused_not_read_as_empty():
    """The editor throws "`nodes` must be a list."; this used to load a graph
    with no nodes at all, so a truncated file passed `plexus check` clean."""
    with pytest.raises(GraphError) as caught:
        Graph.from_yaml("version: 1\nname: truncated\n")
    assert "must be a list" in str(caught.value)


def test_edges_absent_is_no_edges_but_a_non_list_is_refused():
    assert Graph.from_yaml("version: 1\nname: x\nnodes: []\n").edges == []
    with pytest.raises(GraphError):
        Graph.from_yaml("version: 1\nname: x\nnodes: []\nedges: {}\n")


def test_only_disabled_true_disables_a_node():
    """`bool(1)` is true in Python; `1 === true` is false in JavaScript. So
    `disabled: 1` parked a node here and left it running there."""
    for spelling, parked in (("true", True), ("1", False), ('"yes"', False)):
        graph = one_node("    disabled: %s\n" % spelling)
        assert graph.node("t").disabled is parked, spelling


def test_a_label_that_is_not_a_string_is_dropped_rather_than_crashing():
    """
    It was taken as-is, so `Node.name` did `(42).strip()` and raised
    AttributeError — inside `validate`, which every caller expects to return
    problems rather than explode. The editor drops it.
    """
    graph = one_node("    label: 42\n")
    assert graph.node("t").label is None
    assert graph.node("t").name  # does not raise
    validate(graph)  # nor does this


def test_a_host_that_is_not_a_string_is_dropped_too():
    assert one_node("    host: 7\n").node("t").host is None
    assert one_node("    host: workbench\n").node("t").host == "workbench"


def test_a_position_that_is_not_two_numbers_is_the_origin():
    """`int("x")` raised ValueError, which is not a GraphError."""
    for spelling in ('at: ["x", 1]', "at: [1]", 'at: "40,160"', "at: [true, false]"):
        graph = one_node("    %s\n" % spelling)
        assert graph.node("t").position == (0, 0), spelling
    assert one_node("    at: [40, 160]\n").node("t").position == (40, 160)


def test_a_port_reference_that_is_not_a_string_is_refused_by_name():
    """`re.match` on an int raises TypeError; the editor says what is wrong."""
    with pytest.raises(GraphError) as caught:
        Graph.from_yaml(
            "version: 1\nname: x\n"
            "nodes:\n  - id: t\n    type: probe\n"
            "edges:\n  - from: 7\n    to: t:card\n"
        )
    assert "edges[0].from" in str(caught.value)


def test_a_non_string_edge_id_is_generated_rather_than_stringified():
    """`id: 5` gave one graph two different edge ids depending on the reader."""
    graph = Graph.from_yaml(
        "version: 1\nname: x\n"
        "nodes:\n"
        "  - id: t\n    type: probe\n"
        "  - id: c\n    type: capture\n"
        "edges:\n  - id: 5\n    from: t:card\n    to: c:terminal\n"
    )
    assert graph.edges[0].id == "t:card->c:terminal"


def test_hosts_is_the_one_field_that_is_shrugged_at():
    """A non-list is ignored and non-mapping entries are dropped, because that
    is what the editor does with it."""
    shrugged = Graph.from_yaml("version: 1\nname: x\nnodes: []\nhosts: nonsense\n")
    assert shrugged.hosts == []
    graph = Graph.from_yaml(
        "version: 1\nname: x\nnodes: []\nhosts:\n  - nonsense\n  - id: real\n"
    )
    assert [h.id for h in graph.hosts] == ["real"]


def test_a_graph_named_something_that_is_not_a_string_is_untitled():
    assert Graph.from_yaml("version: 1\nname: 5\nnodes: []\n").name == "untitled"

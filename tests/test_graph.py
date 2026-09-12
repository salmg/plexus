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

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "contactless-relay.plexus.yaml"


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


def test_no_terminal_means_no_chain():
    graph = build(MINIMAL)
    graph.nodes = [n for n in graph.nodes if n.id != "t"]
    assert chain(graph) == []

"""
The engine, end to end, with no hardware.

A recorded capture answers like a card and the fingerprint probe asks like a
terminal, so a whole graph runs here: bind, drive, rewrite, report, release.
That is the part worth testing on a laptop. What is left for the rig is the
hardware nodes, and those are exactly the ones that refuse by name.

The capture in tests/captures answers the probe's own sequence — PPSE, the AID
it advertises, GPO with an empty PDOL, and the one record its AFL points at —
so a change to how the probe builds a command shows up here as a miss rather
than as silence.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from plexus.graph import Graph
from plexus.nodes import terminal as terminal_nodes
from plexus.nodes.card import CardUnavailable
from plexus.nodes.terminal import find_all, gpo_for, read_records, tlv_walk
from plexus.runtime import Engine, EngineError, node_implementations
from plexus.runtime.events import Sink

CAPTURE = Path(__file__).resolve().parent / "captures" / "mastercard-probe.jsonl"


def graph_for(body: str) -> Graph:
    return Graph.from_yaml(textwrap.dedent(body))


def probe_over_capture(*, extra_nodes: str = "", extra_edges: str = "") -> Graph:
    """probe -> [anything extra] -> capture."""
    tail = "p" if not extra_nodes else "x"
    return graph_for(
        """
        version: 1
        name: probe over a capture
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: standard}
        %s
          - id: c
            type: capture
            at: [400, 0]
            config: {path: "%s", strict: false}
        edges:
          - from: %s:card
            to: c:terminal
        %s
        """
        % (extra_nodes, CAPTURE.as_posix(), tail, extra_edges)
    )


def run_to_completion(graph: Graph, timeout: float = 10.0) -> Engine:
    engine = Engine(sink=Sink())
    engine.arm(graph)
    assert engine.wait(timeout=timeout), "the driver did not finish"
    engine.stop()
    return engine


# ── The whole thing ─────────────────────────────────────────────────────────


def test_a_probe_over_a_capture_runs_the_whole_sequence():
    engine = run_to_completion(probe_over_capture())

    commands = [e.hex for e in engine.sink.apdus if e.direction == "command"]
    assert commands == [
        "00A404000E325041592E5359532E444446303100",  # SELECT PPSE
        "00A4040007A000000004101000",  # SELECT the AID it advertised
        "80A8000002830000",  # GPO, empty PDOL
        "00B2010C00",  # the one record the AFL points at
    ]
    assert engine.state == "stopped"


def test_every_command_gets_a_response_back():
    engine = run_to_completion(probe_over_capture())
    responses = [e.hex for e in engine.sink.apdus if e.direction == "response"]
    assert len(responses) == 4
    assert all(r.endswith("9000") for r in responses)
    assert responses[-1].startswith("70145A08")  # the record, PAN first


def test_telemetry_reaches_every_node_and_the_edge():
    engine = run_to_completion(probe_over_capture())

    assert engine.telemetry("p").apdus == 4
    assert engine.telemetry("c").apdus == 4
    assert engine.telemetry("p").mean_ms is not None

    edge = engine.edge_telemetry("p:card->c:terminal")
    assert edge.apdus == 4
    assert edge.bytes_out > 0 and edge.bytes_in > 0


def test_the_trace_reads_in_the_order_things_happened():
    """
    A listener that simply prints has to produce a readable trace with no
    sorting of its own. The first cut reported the pair after the exchange
    finished, which put every rewrite above the command that caused it and made
    the timestamps run backwards.
    """
    engine = run_to_completion(probe_over_capture())
    events = engine.sink.apdus

    assert [e.seq for e in events] == sorted(e.seq for e in events)
    assert [e.t for e in events] == sorted(e.t for e in events)
    # Command, response, command, response — a probe never pipelines.
    assert [e.direction for e in events] == ["command", "response"] * 4


def test_a_rewrite_is_reported_after_the_card_answer_it_changed():
    graph = probe_over_capture()
    from plexus.graph import Edge, Node, PortRef

    graph.nodes.insert(
        1,
        Node(id="x", type="mutation", position=(200, 0), config={"playbook": PLAYBOOK}),
    )
    graph.edges = [
        Edge("p->x", PortRef("p", "card"), PortRef("x", "terminal")),
        Edge("x->c", PortRef("x", "card"), PortRef("c", "terminal")),
    ]
    engine = run_to_completion(graph)

    events = engine.sink.apdus
    rewrite = next(e for e in events if e.mutated_by)
    before = events[events.index(rewrite) - 1]

    assert before.direction == "response"
    assert before.mutated_by is None
    assert "5F340101" in before.hex, "the card's own answer should come first"
    assert "5F340107" in rewrite.hex, "then the rewritten one"
    assert rewrite.t >= before.t


def test_stopping_releases_and_says_so():
    engine = run_to_completion(probe_over_capture())
    assert engine.state == "stopped"
    assert all(t.status == "idle" for t in engine._telemetry.values())
    assert any("released" in line.text.lower() for line in engine.sink.logs)


# ── Transforms in the chain ─────────────────────────────────────────────────


def test_a_trace_tap_changes_nothing_and_writes_the_exchange(tmp_path):
    log = tmp_path / "trace.jsonl"
    engine = run_to_completion(
        probe_over_capture(
            extra_nodes="""
          - id: x
            type: trace
            at: [200, 0]
            config: {detail: tlv, path: "%s"}
        """
            % log.as_posix(),
            extra_edges="""
          - from: p:card
            to: x:terminal
        """,
        )
    )

    commands = [e.hex for e in engine.sink.apdus if e.direction == "command"]
    assert commands[0] == "00A404000E325041592E5359532E444446303100"
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 4


PLAYBOOK = textwrap.dedent(
    """
    enabled: true
    log_mutations: false
    pdol_mutations: []
    afl_mutations: []
    dol_mutations: []
    injected_commands: []
    response_mutations:
      - tag: "5F34"
        mode: replace
        value: "07"
        enabled: true
        comment: "PAN sequence number"
    """
)


def test_a_mutation_node_rewrites_and_the_rewrite_is_reported():
    """
    Atrium's engine does the work; Plexus only has to hand it the exchange and
    notice that the bytes came back different.

    Built in Python rather than through a YAML string, because a playbook is
    itself YAML and nesting one in the other by indentation is a way to test
    your own quoting instead of the code.
    """
    graph = probe_over_capture()
    from plexus.graph import Edge, Node, PortRef

    graph.nodes.insert(
        1,
        Node(
            id="x",
            type="mutation",
            position=(200, 0),
            config={"logMutations": False, "playbook": PLAYBOOK},
        ),
    )
    graph.edges = [
        Edge("p->x", PortRef("p", "card"), PortRef("x", "terminal")),
        Edge("x->c", PortRef("x", "card"), PortRef("c", "terminal")),
    ]

    engine = run_to_completion(graph)

    rewrites = [e for e in engine.sink.apdus if e.mutated_by]
    assert rewrites, "the mutation node reported no rewrite"
    assert all(e.mutated_by == "x" for e in rewrites)
    # The record reached the terminal with the replaced sequence number in it.
    assert any("5F340107" in e.hex for e in rewrites)


# ── Refusing ────────────────────────────────────────────────────────────────


def test_a_graph_with_a_blocking_problem_will_not_arm():
    graph = probe_over_capture()
    graph.edges.clear()
    engine = Engine(sink=Sink())
    with pytest.raises(EngineError) as caught:
        engine.arm(graph)
    assert "nothing on its card port" in str(caught.value)
    assert engine.state == "faulted"


def test_a_missing_capture_refuses_at_bind_and_releases():
    graph = graph_for(
        """
        version: 1
        name: no such file
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: quick}
          - id: c
            type: capture
            at: [400, 0]
            config: {path: does/not/exist.jsonl}
        edges:
          - from: p:card
            to: c:terminal
        """
    )
    engine = Engine(sink=Sink())
    with pytest.raises(EngineError):
        engine.arm(graph)
    assert engine.state == "faulted"
    assert not engine.running


def test_a_hardware_node_says_what_it_needs():
    graph = graph_for(
        """
        version: 1
        name: needs a reader
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: quick}
          - id: c
            type: pcsc
            at: [400, 0]
            config: {device: "pcsc:gemalto"}
        edges:
          - from: p:card
            to: c:terminal
        """
    )
    engine = Engine(sink=Sink())
    with pytest.raises(EngineError) as caught:
        engine.arm(graph)
    assert "pyscard" in str(caught.value)


def test_the_build_knows_which_nodes_it_can_bind():
    ready = node_implementations()
    assert ready["capture"] == "ready"
    assert ready["probe"] == "ready"
    assert ready["mutation"] == "ready"
    assert ready["pcsc"] == "pending"
    assert ready["acr122u.target"] == "pending"


def test_an_unimplemented_terminal_names_its_daemon():
    node = terminal_nodes.build("simtrace2", {})
    with pytest.raises(terminal_nodes.TerminalUnavailable) as caught:
        node.drive(None, lambda: False)
    assert "simtrace2-remsim" in str(caught.value)


def test_a_capture_node_with_no_file_refuses():
    with pytest.raises(CardUnavailable):
        from plexus.nodes.card import build

        build("capture", {})


# ── The probe's own reasoning ───────────────────────────────────────────────


def test_tlv_walk_descends_into_constructed_tags():
    ppse = bytes.fromhex(
        "6F20840E325041592E5359532E4444463031A50EBF0C0B61094F07A0000000041010"
    )
    assert find_all(ppse, 0x4F) == [bytes.fromhex("A0000000041010")]


def test_tlv_walk_survives_a_truncated_record():
    """A probe reads what a card chose to send; one bad byte must not lose the rest."""
    assert find_all(bytes.fromhex("5A085413330012345678" + "5F24"), 0x5A) == [
        bytes.fromhex("5413330012345678")
    ]


def test_gpo_sends_zeroes_of_exactly_the_length_the_pdol_asked_for():
    # 6F 0B { A5 09 { 9F38 06 { 9F66 04, 9F02 06 } } } — ten bytes requested.
    fci = bytes.fromhex("6F0BA509" + "9F3806" + "9F66049F0206")
    command = gpo_for(fci)
    assert command[:4] == bytes.fromhex("80A80000")
    assert command[5] == 0x83
    assert command[6] == 10
    assert command[7:17] == bytes(10)


def test_a_multi_byte_pdol_tag_does_not_eat_its_length():
    """9F66 is two tag bytes then a length; getting that wrong shifts everything."""
    from plexus.nodes.terminal import _pdol_length

    assert _pdol_length(bytes.fromhex("9F66049F0206")) == 10
    assert _pdol_length(bytes.fromhex("9505")) == 5  # single-byte tag
    assert _pdol_length(b"") == 0


def test_gpo_with_no_pdol_sends_an_empty_one():
    assert gpo_for(bytes.fromhex("6F02A500")).hex().upper() == "80A8000002830000"


def test_an_unreadable_fci_falls_back_to_an_empty_pdol():
    """
    A card that sends something the walker cannot follow still gets a
    well-formed GPO rather than a crash. It will very likely be refused, and
    that refusal is the card's answer rather than a Plexus failure.
    """
    assert gpo_for(bytes.fromhex("6FFF00")).hex().upper() == "80A8000002830000"


def test_read_records_follows_the_afl():
    gpo = bytes.fromhex("770A82021C00" + "9404" + "08010200")
    assert [c.hex().upper() for c in read_records(gpo)] == ["00B2010C00", "00B2020C00"]


def test_read_records_ignores_a_nonsense_afl_entry():
    gpo = bytes.fromhex("7706" + "9404" + "00000000")
    assert read_records(gpo) == []


def test_a_deep_probe_sweeps_when_there_is_no_afl():
    assert read_records(bytes.fromhex("7700"), deep=False) == []
    assert len(read_records(bytes.fromhex("7700"), deep=True)) == 40


def test_the_walker_is_not_fooled_by_padding():
    assert list(tlv_walk(bytes.fromhex("00FF5A020102"))) == [(0x5A, bytes.fromhex("0102"))]

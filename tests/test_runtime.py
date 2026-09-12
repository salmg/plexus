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
import time
from pathlib import Path

import pytest

from plexus.graph import Graph, PortRef, validate
from plexus.nodes import terminal as terminal_nodes
from plexus.nodes.card import CardUnavailable
from plexus.nodes.terminal import find_all, gpo_for, read_records, tlv_walk
from plexus.runtime import Engine, EngineError, node_implementations
from plexus.runtime.events import Sink

REPO = Path(__file__).resolve().parent.parent
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


def test_a_finished_terminal_leaves_the_card_bound():
    """
    A probe runs out of script; that is not a reason to drop the card. `tx` is
    the most useful thing in the tool right after a probe has told you what the
    card is, and releasing here would take it away. The nodes stop claiming to
    be live, and nothing else changes until someone stops it.
    """
    engine = Engine(sink=Sink())
    engine.arm(probe_over_capture())
    assert engine.wait(timeout=10.0)

    assert engine.telemetry("p").status == "ready"
    assert engine.telemetry("p").apdus == 4
    assert engine._card is not None, "the card should still be bound"

    # And it still answers.
    response = engine.transmit(bytes.fromhex("00A404000E325041592E5359532E444446303100"))
    assert response.endswith(b"\x90\x00")

    engine.stop()
    assert engine._card is None
    assert engine.telemetry("p").status == "idle"


def test_stopping_releases_and_says_so():
    engine = run_to_completion(probe_over_capture())
    assert engine.state == "stopped"
    assert all(t.status == "idle" for t in engine._telemetry.values())
    assert any("released" in line.text.lower() for line in engine.sink.logs)


# ── Transforms in the chain ─────────────────────────────────────────────────


def test_a_trace_tap_changes_nothing_and_writes_the_exchange():
    """
    Written inside the repo on purpose: a node may only reach paths inside the
    workspace, and the workspace has to hold both the capture being read and
    the trace being written. See tests/test_security.py.
    """
    log = REPO / "logs" / "test-trace-tap.jsonl"
    if log.exists():
        log.unlink()

    try:
        engine = run_to_completion(
            probe_over_capture(
                extra_nodes="""
          - id: x
            type: trace
            at: [200, 0]
            config: {detail: tlv, path: "logs/test-trace-tap.jsonl"}
        """,
                extra_edges="""
          - from: p:card
            to: x:terminal
        """,
            )
        )

        commands = [e.hex for e in engine.sink.apdus if e.direction == "command"]
        assert commands[0] == "00A404000E325041592E5359532E444446303100"
        assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 4
    finally:
        if log.exists():
            log.unlink()


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




def test_a_rewrite_is_drawn_on_the_edge_it_leaves_on():
    """
    With one transform, "the edge out of this node" and "the edge into the
    card" are the same edge, so taking the ends of the list looked right. With
    two, every rewrite was drawn at one end of the chain or the other — a
    mutation in the middle appeared to happen somewhere it did not.

    probe -> trace -> mutation -> capture, and the mutation is second on
    purpose: it rewrites a *response*, which travels back upstream, so it
    leaves on the edge the mutation node was reached by — `tr->x`. The old
    rule said `p->tr`, the first edge in the chain, which the mutation node
    never touches.
    """
    from plexus.graph import Edge, Node, PortRef

    log = REPO / "logs" / "test-attribution.jsonl"
    if log.exists():
        log.unlink()

    graph = probe_over_capture()
    graph.nodes.insert(
        1,
        Node(
            id="tr",
            type="trace",
            position=(200, 0),
            config={"detail": "raw", "path": "logs/test-attribution.jsonl"},
        ),
    )
    graph.nodes.insert(
        2,
        Node(
            id="x",
            type="mutation",
            position=(300, 0),
            config={"logMutations": False, "playbook": PLAYBOOK},
        ),
    )
    graph.edges = [
        Edge("p->tr", PortRef("p", "card"), PortRef("tr", "terminal")),
        Edge("tr->x", PortRef("tr", "card"), PortRef("x", "terminal")),
        Edge("x->c", PortRef("x", "card"), PortRef("c", "terminal")),
    ]

    try:
        engine = run_to_completion(graph)

        rewrites = [e for e in engine.sink.apdus if e.mutated_by == "x"]
        assert rewrites, "the mutation node reported no rewrite"
        assert all(e.direction == "response" for e in rewrites)
        assert {e.edge for e in rewrites} == {"tr->x"}, (
            "a response rewritten by x leaves on the edge x was reached by"
        )
    finally:
        if log.exists():
            log.unlink()


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
    assert list(tlv_walk(bytes.fromhex("00FF5A020102"))) == [
        (0x5A, bytes.fromhex("0102"))
    ]


# ── Things that were wired to nothing ───────────────────────────────────────


def test_arming_a_second_graph_forgets_the_first():
    """
    `_telemetry` was only ever written per-node by `_bind`, never cleared, and
    `Session._hello` replays all of it — so a reconnecting editor was told about
    nodes that were not in the graph it was looking at.
    """
    engine = Engine(sink=Sink())
    engine.arm(probe_over_capture())
    assert engine.wait(timeout=10.0)
    engine.stop()

    first = set(engine._telemetry)
    assert "p" in first and "c" in first

    renamed = probe_over_capture()
    for node in renamed.nodes:
        node.id = node.id + "-second"
    for edge in renamed.edges:
        edge.source = PortRef(edge.source.node + "-second", edge.source.port)
        edge.target = PortRef(edge.target.node + "-second", edge.target.port)

    engine.arm(renamed)
    assert engine.wait(timeout=10.0)
    engine.stop()

    assert set(engine._telemetry) == {"p-second", "c-second"}, (
        "stale nodes from the first graph survived: %s" % sorted(engine._telemetry)
    )
    # Edge *ids* come from the file and are not derived from node ids, so the
    # requirement is that they are this graph's edges — not that they were
    # renamed along with the nodes.
    assert set(engine._edge_ids) == {e.id for e in renamed.edges}
    assert set(engine._edges) == {e.id for e in renamed.edges}


def test_the_timing_node_is_actually_called():
    """
    `Swtx.note_elapsed` existed, said in its own docstring that the engine
    called it after each exchange, and was called by nothing — so the Timing
    node bound, passed traffic through and counted zero.
    """
    from plexus.graph import Edge, Node
    from plexus.graph import PortRef as Ref

    graph = probe_over_capture()
    graph.nodes.insert(
        1,
        Node(
            id="clock",
            type="swtx",
            position=(200, 0),
            # Zero threshold so every exchange counts as slow; a replayed
            # capture answers in microseconds and would never trip a real one.
            config={"wtxm": 4, "thresholdMs": 0},
        ),
    )
    graph.edges = [
        Edge("p->clock", Ref("p", "card"), Ref("clock", "terminal")),
        Edge("clock->c", Ref("clock", "card"), Ref("c", "terminal")),
    ]

    engine = Engine(sink=Sink())
    engine.arm(graph)
    assert engine.wait(timeout=10.0)

    node = engine._transform_by_id["clock"]
    assert node.requested == 4, (
        "expected one request per exchange, got %d" % node.requested
    )
    assert engine.telemetry("clock").detail, "nothing surfaced that it fired"
    assert any("more time" in line.text for line in engine.sink.logs)

    engine.stop()


def test_a_slow_card_below_the_threshold_asks_for_nothing():
    """The counter has to mean something: a fast card must not trip it."""
    from plexus.graph import Edge, Node
    from plexus.graph import PortRef as Ref

    graph = probe_over_capture()
    graph.nodes.insert(
        1,
        Node(id="clock", type="swtx", position=(200, 0), config={"thresholdMs": 60_000}),
    )
    graph.edges = [
        Edge("p->clock", Ref("p", "card"), Ref("clock", "terminal")),
        Edge("clock->c", Ref("clock", "card"), Ref("c", "terminal")),
    ]

    engine = Engine(sink=Sink())
    engine.arm(graph)
    assert engine.wait(timeout=10.0)
    assert engine._transform_by_id["clock"].requested == 0
    engine.stop()


def test_a_chain_broken_by_a_disabled_node_binds_nothing():
    """
    Parity has teeth here: when `chain` walked into disabled nodes the engine
    armed four nodes on a graph the editor drew as three, and drove a node the
    editor had greyed out. Truncated, the chain is one node long, and one node
    is not a conversation — so `arm` refuses before opening anything.
    """
    graph = probe_over_capture(
        extra_nodes="""
          - id: x
            type: trace
            at: [200, 0]
            config: {detail: raw, path: "logs/parity.jsonl"}
        """,
        extra_edges="""
          - from: p:card
            to: x:terminal
        """,
    )
    graph.node("x").disabled = True

    engine = Engine(sink=Sink())
    with pytest.raises(EngineError) as refused:
        engine.arm(graph)
    assert "No complete chain" in str(refused.value)
    assert engine.state == "faulted"
    # Nothing opened: no card, no transforms, no telemetry for a parked node.
    assert engine._card is None
    assert engine._transforms == []
    assert "x" not in engine._telemetry


# ── Breakpoints ─────────────────────────────────────────────────────────────
#
# The catalogue has always said a breakpoint holds a command "until you release
# it from the console". For a while nothing could: no verb reached
# `Breakpoint.release`, so a hit wedged the run, `tx` parked the thread that
# would have carried the release, and `stop` was the only way out — and `stop`
# called `release` rather than `close`, which re-armed the breakpoint for the
# driver's next command. None of this had a test. This is that test.


def breakpoint_over_capture(match_ins: str = "A8", editable: bool = True) -> Graph:
    return probe_over_capture(
        extra_nodes="""
          - id: x
            type: breakpoint
            at: [200, 0]
            config: {matchIns: "%s", editable: %s}
        """
        % (match_ins, "true" if editable else "false"),
        extra_edges="""
          - from: p:card
            to: x:terminal
        """,
    )


def until(predicate, timeout: float = 5.0, what: str = "the condition") -> None:
    """Spin until `predicate()`, or fail. The driver runs on its own thread."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("%s never became true within %.1fs" % (what, timeout))


def test_a_breakpoint_announces_the_command_it_is_holding():
    """
    A silent breakpoint is an unreleasable one: the node id and the bytes are
    exactly what you need in order to release it, and for a while the only way
    to learn either was to read the source.
    """
    engine = Engine(sink=Sink())
    engine.arm(breakpoint_over_capture())
    try:
        until(lambda: engine.held() == ["x"], what="the breakpoint holding")

        said = [line.text for line in engine.sink.logs if "holding" in line.text]
        assert any(text.startswith("x is holding 80A8000002830000") for text in said)
        assert engine.telemetry("x").detail == "holding 80A8000002830000"
    finally:
        engine.stop()


def test_a_held_breakpoint_is_released_from_the_console():
    from plexus.api import console

    engine = Engine(sink=Sink())
    graph = breakpoint_over_capture()
    engine.arm(graph)
    try:
        until(lambda: engine.held() == ["x"], what="the breakpoint holding")

        lines, level = console.run(engine, graph, "release")
        assert level is None
        assert lines == ["x released."]

        assert engine.wait(timeout=5.0), "the driver did not finish after release"
        commands = [e.hex for e in engine.sink.apdus if e.direction == "command"]
        assert "80A8000002830000" in commands
        assert engine.held() == []
    finally:
        engine.stop()


def test_a_held_command_can_be_released_as_different_bytes():
    from plexus.api import console

    engine = Engine(sink=Sink())
    graph = breakpoint_over_capture()
    engine.arm(graph)
    try:
        until(lambda: engine.held() == ["x"], what="the breakpoint holding")
        lines, level = console.run(engine, graph, "release 80A8000002831234")
        assert level is None
        assert lines == ["x released as 80A8000002831234."]
        # The card was asked the replacement, not what the probe wrote.
        until(
            lambda: any(
                e.hex == "80A8000002831234"
                for e in engine.sink.apdus
                if e.direction == "command"
            ),
            what="the replacement reaching the card",
        )
    finally:
        engine.stop()


def test_a_breakpoint_that_forbids_editing_says_so():
    from plexus.api import console

    engine = Engine(sink=Sink())
    graph = breakpoint_over_capture(editable=False)
    engine.arm(graph)
    try:
        until(lambda: engine.held() == ["x"], what="the breakpoint holding")
        lines, level = console.run(engine, graph, "release 80A8000002831234")
        assert level == "error"
        assert "does not allow editing" in lines[0]
        assert engine.held() == ["x"], "a refused release must not let it go"
    finally:
        engine.stop()


def test_release_with_nothing_held_says_so_rather_than_waiting():
    from plexus.api import console

    engine = Engine(sink=Sink())
    lines, level = console.run(engine, None, "release")
    assert level == "error"
    assert "Nothing is held" in lines[0]


def test_tx_refuses_while_a_breakpoint_is_holding():
    """
    `tx` used to park on the chain lock the held exchange owns — on the thread
    the server reads frames from, so the `release` that would free it could
    never arrive. It refuses, and names what is in the way.
    """
    from plexus.api import console

    engine = Engine(sink=Sink())
    graph = breakpoint_over_capture()
    engine.arm(graph)
    try:
        until(lambda: engine.held() == ["x"], what="the breakpoint holding")

        started = time.monotonic()
        lines, level = console.run(engine, graph, "tx 00B2010C00")
        assert time.monotonic() - started < 1.0, "tx waited instead of refusing"
        assert level == "error"
        assert "x is holding a command" in lines[0]
    finally:
        engine.stop()


def test_releasing_a_breakpoint_does_not_disarm_it():
    """
    Stepping: release the held command and the next one is caught too. This is
    the intended behaviour of the node, and it is also the reason `stop` cannot
    use `release` — see the test below.
    """
    from plexus.api import console

    engine = Engine(sink=Sink())
    graph = breakpoint_over_capture(match_ins="")
    engine.arm(graph)
    try:
        until(lambda: engine.held() == ["x"], what="the first command being held")
        first = engine._transform_by_id["x"].held

        console.run(engine, graph, "release")

        until(
            lambda: engine.held() == ["x"]
            and engine._transform_by_id["x"].held not in (None, first),
            what="the next command being held too",
        )
    finally:
        engine.stop()


def test_stop_disarms_the_breakpoint_rather_than_merely_releasing_it(monkeypatch):
    """
    `stop` used to call `release`, which sets the event and leaves the
    breakpoint armed. `_release` closes it a moment later, so most of the time
    the stop flag wins: the driver checks it and leaves without sending
    anything else, and nothing shows.

    The window is between the release loop and the join. A driver that sends
    two commands per stop check is inside that window by construction, so this
    is the race made deterministic: the second command is caught by a
    breakpoint nobody will release, the join expires, and the card is
    disconnected under a live exchange. `close` disarms, so there is no second
    catch.
    """

    class TwoPerCheck:
        """A terminal that asks twice between stop checks. Real ones do too."""

        def drive(self, channel, stop):
            while not stop():
                channel.transmit(bytes.fromhex("00A404000E325041592E5359532E444446303100"))
                channel.transmit(bytes.fromhex("00A4040007A000000004101000"))

    monkeypatch.setattr(
        terminal_nodes, "build", lambda node_type, config: TwoPerCheck()
    )

    engine = Engine(sink=Sink())
    engine.arm(breakpoint_over_capture(match_ins=""))
    until(lambda: engine.held() == ["x"], what="the first command being held")

    started = time.monotonic()
    engine.stop(timeout=2.0)
    elapsed = time.monotonic() - started

    warnings = [line.text for line in engine.sink.logs if line.level == "warn"]
    assert not [w for w in warnings if "did not stop in time" in w], (
        "the driver was caught again by a breakpoint stop had only released"
    )
    assert not [w for w in warnings if "still in flight" in w]
    assert elapsed < 1.5, "stop waited out the join (%.1fs)" % elapsed
    assert engine.state == "stopped"


def test_arming_again_releases_what_the_last_run_left_bound():
    """
    `running` only asks whether the relay thread is alive, and a finished probe
    leaves it dead with the card still open on purpose — that is what keeps
    `tx` useful. The editor shows Run again in exactly that state, so the next
    press reached `arm` with a card bound, and `_bind` overwrote it without
    closing it: one reader claimed twice, and nothing holding the first claim.
    """
    engine = Engine(sink=Sink())
    graph = probe_over_capture()

    engine.arm(graph)
    assert engine.wait(timeout=5.0), "the driver did not finish"
    assert engine.running is False
    first = engine._card
    assert first is not None, "a finished probe keeps the card bound"

    closed = []
    first.disconnect = lambda: closed.append("card")

    engine.arm(graph)
    try:
        assert closed == ["card"], "the first card was left claimed"
        assert engine._card is not first
        assert any(
            "Releasing what the last run left bound" in line.text
            for line in engine.sink.logs
        )
    finally:
        engine.stop()


def test_arming_again_after_a_fault_releases_too():
    """
    The other path that leaves a dead thread over a bound rig:
    `_release_if_idle` keeps everything so a fault can be inspected.
    """
    engine = Engine(sink=Sink())
    graph = probe_over_capture()
    engine.arm(graph)
    assert engine.wait(timeout=5.0)

    # Stand in for a driver that died: mark faulted with the rig still bound,
    # which is the state `_release_if_idle` produces.
    engine._set_state("faulted", "stand-in")
    first = engine._card
    closed = []
    first.disconnect = lambda: closed.append("card")

    engine.arm(graph)
    try:
        assert closed == ["card"]
    finally:
        engine.stop()


def test_a_refused_graph_leaves_the_previous_rig_alone():
    """
    A Run you get wrong must cost you nothing. `arm` used to clear the previous
    run's telemetry and release its card *before* discovering that the new
    graph would not run, so one typo took away a card session that was still
    perfectly usable — and `tx` with it.
    """
    engine = Engine(sink=Sink())
    good = probe_over_capture()
    engine.arm(good)
    assert engine.wait(timeout=5.0)
    card = engine._card
    assert card is not None, "a finished probe keeps the card bound"

    broken = probe_over_capture()
    broken.edges = []

    with pytest.raises(EngineError):
        engine.arm(broken)
    try:
        assert engine._card is card, "the previous card was released"
        assert engine.state == "running", "the previous run was declared faulted"
        assert set(engine._telemetry) == {"p", "c"}, "its telemetry was wiped"
        # And it is still usable, which is the whole point.
        assert engine.transmit(bytes.fromhex("00B2010C00")).hex().upper().startswith(
            "70145A08"
        )
        assert any(
            "still bound" in line.text
            for line in engine.sink.logs
            if line.level == "warn"
        )
    finally:
        engine.stop()


def test_a_refused_graph_with_nothing_bound_still_faults():
    """The honest answer to a Run that did not run, when there was nothing to
    keep."""
    engine = Engine(sink=Sink())
    broken = probe_over_capture()
    broken.edges = []

    with pytest.raises(EngineError):
        engine.arm(broken)
    assert engine.state == "faulted"


# ── The out-of-band line ────────────────────────────────────────────────────
#
# The inject node had no runtime test at all, which is how three things
# survived: it injected with no dashed edge drawn, it injected into the card
# even when the dashed edge pointed somewhere else, and when it did inject the
# traffic left no trace — no APDU event, and the dashed edge's own telemetry
# stayed at zero for the life of the run.


def inject_over_capture(*, oob: bool = True, oob_to: str = "c") -> Graph:
    """probe -> inject -> capture, with the dashed edge drawn or not."""
    # Leading newline: the template substitutes this after eight spaces of its
    # own indentation, which is what every other caller's triple-quoted string
    # is doing too.
    extra_edges = "\n          - from: p:card\n            to: x:terminal\n"
    if oob:
        extra_edges += "          - from: x:oob\n            to: %s:terminal\n" % oob_to
    return probe_over_capture(
        extra_nodes="""
          - id: x
            type: inject
            at: [200, 0]
            config: {triggerIns: "A8", apdu: "80CA9F1700", when: after_response}
        """,
        extra_edges=extra_edges,
    )


def test_an_injection_leaves_a_trace_on_the_wire_and_on_its_edge():
    engine = run_to_completion(inject_over_capture())

    injected = [e for e in engine.sink.apdus if e.note == "out of band"]
    assert [e.direction for e in injected] == ["command", "response"]
    assert injected[0].hex == "80CA9F1700"
    assert injected[0].mutated_by == "x"
    # Attributed to the dashed edge, not to the chain.
    assert injected[0].edge == "x:oob->c:terminal"

    edge = engine.edge_telemetry("x:oob->c:terminal")
    assert edge.apdus == 1
    assert edge.bytes_out == 5
    assert edge.bytes_in > 0

    assert engine.telemetry("x").detail == "injected 1"


def test_nothing_is_injected_without_a_dashed_edge():
    """
    The engine handed every transform a line to the card whether one was drawn
    or not, so an inject node with no dashed edge injected anyway — a command
    the graph did not authorise reaching a real card.
    """
    graph = inject_over_capture(oob=False)

    # Said before Run, too: the editor shows this as a warning.
    codes = [p.code for p in validate(graph)]
    assert "unwired-oob" in codes

    engine = run_to_completion(graph)
    assert not [e for e in engine.sink.apdus if e.note == "out of band"]
    # And the node still passed traffic through, which is the other half of the
    # promise: no dashed edge disables the injection, not the node.
    assert engine.telemetry("x").apdus > 0


def test_a_dashed_edge_to_somewhere_else_is_refused_at_bind():
    """It pointed at another node and the injection still went to the card."""
    graph = inject_over_capture(oob_to="p")
    # p:terminal does not exist, so wire the dashed line to a second card node
    # that is not the one being driven.
    graph = Graph.from_yaml(
        """
        version: 1
        name: two cards
        nodes:
          - id: p
            type: probe
            at: [0, 0]
            config: {depth: standard}
          - id: x
            type: inject
            at: [200, 0]
            config: {triggerIns: "A8", apdu: "80CA9F1700"}
          - id: c
            type: capture
            at: [400, 0]
            config: {path: "%s", strict: false}
          - id: other
            type: capture
            at: [400, 200]
            config: {path: "%s", strict: false}
        edges:
          - from: p:card
            to: x:terminal
          - from: x:card
            to: c:terminal
          - from: x:oob
            to: other:terminal
        """
        % (CAPTURE.as_posix(), CAPTURE.as_posix())
    )

    engine = Engine(sink=Sink())
    with pytest.raises(EngineError) as refused:
        engine.arm(graph)
    assert "out-of-band line goes to other" in str(refused.value)
    assert engine.state == "faulted"
    engine.stop()


def test_an_injection_keeps_only_the_last_response():
    """`results` held every response for the life of the run and nothing read
    it."""
    from plexus.nodes.transform import build as build_transform

    answers = [b"\x90\x00", b"\x6a\x82"]
    node = build_transform(
        "inject",
        {"triggerIns": "A8", "apdu": "80CA9F1700", "repeat": True},
        card_execute=lambda apdu: answers.pop(0),
    )
    for _ in range(2):
        node.on_response(bytes.fromhex("80A8000002830000"), b"\x90\x00")

    assert node.fired == 2
    assert node.last_response == b"\x6a\x82"
    assert not hasattr(node, "results")


def test_an_injection_with_no_line_counts_the_miss():
    """The comment said the miss was counted; the code returned before
    counting anything."""
    from plexus.nodes.transform import build as build_transform

    node = build_transform("inject", {"triggerIns": "A8", "apdu": "80CA9F1700"})
    node.on_response(bytes.fromhex("80A8000002830000"), b"\x90\x00")
    assert node.fired == 0
    assert node.skipped == 1

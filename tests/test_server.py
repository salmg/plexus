"""
The server, driven the way the editor drives it.

These speak the protocol rather than calling Python: connect, hello, arm, watch
the frames arrive, type at the console, stop. If PROTOCOL.md and
`ui/src/runtime/ws.ts` and this file ever disagree, one of them is a bug, and
this is the one that can fail a build.

The rig under test is the one that needs no hardware — a probe asking a
recorded capture — so the whole path is exercised on any machine.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict

import pytest

# Beside this file, not inside it: tests/test_security.py reads frames too, and
# `from tests.test_server import collect` only resolves when pytest is started
# from the repository root.
from conftest import needs_atrium
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from frames import (
    FRAME_BUDGET_S,
    collect,
    collect_state,
    collect_until,
    pull,
    unwrap,
)

from plexus.api import server as server_module
from plexus.api import session as session_module
from plexus.api.server import create_app, host_ok, is_loopback, token_ok
from plexus.api.session import _normalise
from plexus.graph import Graph, GraphError

CAPTURE = Path(__file__).resolve().parent / "captures" / "mastercard-probe.jsonl"
EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "probe-a-capture.plexus.yaml"
)


# Everything below drives a graph, and driving a graph needs the submodule.
# See tests/conftest.py.
pytestmark = needs_atrium

def graph_frame() -> Dict[str, Any]:
    """The example rig in the shape the editor sends it."""
    return {
        "version": 1,
        "name": "probe a recorded card",
        "hosts": [],
        "nodes": [
            {
                "id": "p",
                "type": "probe",
                "position": {"x": 0, "y": 0},
                "config": {"depth": "standard"},
            },
            {
                "id": "c",
                "type": "capture",
                "position": {"x": 400, "y": 0},
                "config": {"path": CAPTURE.as_posix(), "strict": False},
            },
        ],
        "edges": [
            {
                "id": "p:card->c:terminal",
                "from": {"node": "p", "port": "card"},
                "to": {"node": "c", "port": "terminal"},
            }
        ],
    }


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


# ── Handshake ───────────────────────────────────────────────────────────────


def test_hello_is_answered_with_enough_to_catch_up(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        seen = collect(socket, until="state")
        assert {f["type"] for f in seen} >= {"devices", "state"}
        assert next(f for f in seen if f["type"] == "state")["state"] == "stopped"


def test_the_wrong_protocol_version_is_refused(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 99})
        frame = socket.receive_json()
        assert frame["type"] == "error"
        assert "protocol" in frame["message"]


def test_an_unknown_frame_says_so_rather_than_dying(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "nonsense"})
        assert any(
            f.get("type") == "error" for f in collect(socket, until="error")
        )


def test_waiting_for_a_frame_that_never_comes_fails_rather_than_hanging(client):
    """
    The bound on the collectors, checked.

    Before `pull` had a deadline this test could not have failed — it would
    have hung, taking the suite with it. That is how a missing capture
    presented itself on every machine but the one that wrote the capture.
    """
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")

        started = time.monotonic()
        with pytest.raises(AssertionError) as absent:
            collect(socket, until="no-such-frame-type", budget=0.5)
        assert time.monotonic() - started < 10.0
        assert "within 0.5s" in str(absent.value)

        started = time.monotonic()
        with pytest.raises(AssertionError) as never:
            collect_until(socket, lambda seen: False, budget=0.5)
        assert time.monotonic() - started < 10.0
        assert "condition never met within 0.5s" in str(never.value)


# ── Arming and running ──────────────────────────────────────────────────────



def test_arming_a_graph_runs_it_and_streams_the_exchange(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")

        socket.send_json({"type": "arm", "graph": graph_frame()})

        def commands_of(seen):
            return [
                f["event"]["hex"]
                for f in seen
                if f["type"] == "apdu" and f["event"]["dir"] == "command"
            ]

        seen = collect_until(socket, lambda s: len(commands_of(s)) >= 4)

        states = [f["state"] for f in seen if f["type"] == "state"]
        assert "running" in states
        assert "faulted" not in states

        commands = commands_of(seen)
        assert commands[0] == "00A404000E325041592E5359532E444446303100"
        assert commands[:4] == [
            "00A404000E325041592E5359532E444446303100",
            "00A4040007A000000004101000",
            "80A8000002830000",
            "00B2010C00",
        ]

        assert any(f["type"] == "node" for f in seen)
        assert any(f["type"] == "edge" for f in seen)


def test_a_finished_probe_leaves_the_runtime_running_and_the_card_bound(client):
    """
    Running out of script is not stopping. The editor's button stays on Stop,
    because there is still something to stop — and `tx` still reaches the card.
    """
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph_frame()})

        seen = collect_until(
            socket,
            lambda s: any(
                f["type"] == "log" and "still bound" in f["line"]["text"] for f in s
            ),
        )
        assert "stopped" not in [f["state"] for f in seen if f["type"] == "state"]

        socket.send_json({"type": "command", "id": "t", "line": "tx 00B2010C00"})
        result = next(f for f in collect(socket, until="result") if f["type"] == "result")
        assert result["lines"][1].startswith("<- 70145A08")


def test_a_graph_that_cannot_run_comes_back_as_an_error_not_a_drop(client):
    broken = graph_frame()
    broken["edges"] = []

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": broken})
        error = next(f for f in collect(socket, until="error") if f["type"] == "error")
        assert "card port" in error["message"]


def test_arm_without_a_graph_is_refused(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm"})
        error = next(f for f in collect(socket, until="error") if f["type"] == "error")
        assert "graph" in error["message"]


# ── The console ─────────────────────────────────────────────────────────────


def test_a_console_command_is_answered_by_its_id(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")

        socket.send_json({"type": "command", "id": "c1", "line": "help"})
        result = next(f for f in collect(socket, until="result") if f["type"] == "result")
        assert result["id"] == "c1"
        assert any("status" in line for line in result["lines"])


def test_an_unknown_console_verb_answers_at_error_level(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "command", "id": "c9", "line": "frobnicate"})
        result = next(f for f in collect(socket, until="result") if f["type"] == "result")
        assert result["level"] == "error"
        assert "Unknown command" in result["lines"][0]


def test_status_reports_the_chain_and_the_round_trip(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph_frame()})
        collect(socket, until="state")

        socket.send_json({"type": "command", "id": "s", "line": "status"})
        result = next(f for f in collect(socket, until="result") if f["type"] == "result")
        body = "\n".join(result["lines"])
        assert "p -> c" in body
        assert "out and back" in body


def test_tx_pushes_an_apdu_by_hand_and_shows_both_halves(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph_frame()})
        collect(socket, until="state")

        socket.send_json(
            {
                "type": "command",
                "id": "t",
                "line": "tx 00A404000E325041592E5359532E444446303100",
            }
        )
        result = next(f for f in collect(socket, until="result") if f["type"] == "result")
        assert result["lines"][0].startswith("-> 00A404000E")
        assert result["lines"][1].startswith("<- 6F20")


def test_tx_refuses_bytes_that_are_not_hex(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "command", "id": "t", "line": "tx zzz"})
        result = next(f for f in collect(socket, until="result") if f["type"] == "result")
        assert result["level"] == "error"


def test_stop_releases_and_reports_stopped(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph_frame()})
        collect(socket, until="state")

        socket.send_json({"type": "stop"})
        seen = collect_state(socket, "stopped")
        states = [f["state"] for f in seen if f["type"] == "state"]
        # Stopping is announced before it has happened, so an editor can grey
        # the button out rather than wait for hardware to let go.
        assert "stopping" in states
        assert any("released" in f["line"]["text"] for f in seen if f["type"] == "log")


# ── The graph the editor sends ──────────────────────────────────────────────


def test_the_editors_json_and_the_saved_yaml_describe_the_same_graph():
    """
    The editor posts JSON and the file is YAML. If those two shapes ever mean
    different graphs, a rig would behave differently depending on whether it
    was saved first.
    """
    from_file = Graph.load(EXAMPLE)
    posted = _normalise(
        {
            "version": 1,
            "name": from_file.name,
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "position": {"x": n.position[0], "y": n.position[1]},
                    "config": n.config,
                    **({"label": n.label} if n.label else {}),
                }
                for n in from_file.nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "from": {"node": e.source.node, "port": e.source.port},
                    "to": {"node": e.target.node, "port": e.target.port},
                }
                for e in from_file.edges
            ],
        }
    )

    import yaml

    rebuilt = Graph.from_yaml(yaml.safe_dump(posted, sort_keys=False))
    assert [n.id for n in rebuilt.nodes] == [n.id for n in from_file.nodes]
    assert [str(e.source) for e in rebuilt.edges] == [
        str(e.source) for e in from_file.edges
    ]
    assert rebuilt.node("mutation-1").config == from_file.node("mutation-1").config


def test_a_malformed_graph_frame_is_a_message_not_a_traceback():
    with pytest.raises(GraphError):
        _normalise({"nodes": ["not an object"]})


# ── Guards ──────────────────────────────────────────────────────────────────


def test_loopback_is_recognised_however_it_is_written():
    assert is_loopback("127.0.0.1")
    assert is_loopback("localhost")
    assert is_loopback("::1")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("192.168.1.24")


def test_a_rebinding_host_header_is_refused():
    """DNS rebinding defeats same-origin; the Host header still names the attacker."""
    assert host_ok("127.0.0.1:8900")
    assert host_ok("localhost:8900")
    assert not host_ok("evil.example.com")
    assert not host_ok("evil.example.com:8900")
    assert not host_ok(None)


def test_a_token_is_only_required_when_one_is_configured(monkeypatch):
    monkeypatch.delenv(server_module.TOKEN_VARIABLE, raising=False)
    assert token_ok(None)

    monkeypatch.setenv(server_module.TOKEN_VARIABLE, "sekrit")
    assert token_ok("sekrit")
    assert not token_ok("wrong")
    assert not token_ok(None)


def test_binding_beyond_loopback_without_a_token_refuses_to_start(monkeypatch):
    monkeypatch.delenv(server_module.TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as caught:
        server_module.guard_bind("0.0.0.0")
    assert "Refusing to bind" in str(caught.value)

    monkeypatch.setenv(server_module.TOKEN_VARIABLE, "sekrit")
    server_module.guard_bind("0.0.0.0")  # allowed, with a warning


def test_a_socket_without_the_token_is_closed(monkeypatch):
    monkeypatch.setenv(server_module.TOKEN_VARIABLE, "sekrit")
    with TestClient(create_app()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws") as socket:
                socket.receive_json()


def test_health_says_what_this_build_can_bind(client):
    body = client.get("/api/health").json()
    assert body["protocol"] == 1
    assert body["nodes"]["capture"] == "ready"
    assert body["nodes"]["pcsc"] == "pending"


def test_stop_overtakes_a_console_command_that_is_still_running(client, monkeypatch):
    """
    The read loop used to await each frame's handler, and `command` can block
    on a card. So `stop` sat unread behind it: measured at 4.01s for a
    four-second command, with the editor's button stuck on Stop throughout.

    A slow `console.run` stands in for a card that has not answered.
    """
    started = threading.Event()

    def slow_run(engine, graph, line):
        started.set()
        time.sleep(2.0)
        return (["-> slow", "<- slow"], None)

    from plexus.api import console as console_module

    monkeypatch.setattr(console_module, "run", slow_run)

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")

        socket.send_json({"type": "command", "id": "slow", "line": "tx 00B2010C00"})
        assert started.wait(2.0), "the command never started"

        asked = time.monotonic()
        socket.send_json({"type": "stop"})
        collect_state(socket, "stopped")
        answered = time.monotonic() - asked

        assert answered < 1.5, (
            "stop waited for the command to finish (%.2fs)" % answered
        )
        # Nothing was armed, so it goes straight to stopped — the point is
        # that the frame was read at all while the command was still out.

        # And the command still answers, on its own time, by its own id.
        result = next(
            f for f in collect(socket, until="result") if f["type"] == "result"
        )
        assert result["id"] == "slow"


# ── Losing the socket ───────────────────────────────────────────────────────


def test_the_run_survives_the_socket_and_a_reconnect_catches_up(client):
    """
    PROTOCOL.md's promise, which was false when it was written: every
    connection built its own Engine and `Session.close` stopped it, so a
    browser reload released the card mid-transaction and the reconnecting
    editor was talking to a different engine that had never been armed. The
    replay in `_hello` was written for this and had nothing to replay.
    """
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph_frame()})
        first = collect_state(socket, "running")
        assert [f["state"] for f in first if f["type"] == "state"][-1] == "running"

    # Socket gone. The probe runs out of script and settles, which keeps the
    # card bound on purpose — so there is genuinely something to come back to.
    #
    # Waited for rather than assumed: the server tears the connection down
    # asynchronously, and reconnecting before it has finished would test the
    # race rather than the reconnect.
    runtime = client.app.state.runtime
    deadline = time.monotonic() + 5.0
    while runtime.listeners and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.listeners == 0, "the server never finished with the closed socket"

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        # `_hello` sends devices, then state, then one node frame per bound
        # node — so waiting for "a state frame" stops before the replay.
        seen = collect_until(
            socket,
            lambda s: {f["id"] for f in s if f["type"] == "node"} >= {"p", "c"},
        )

        state = next(f for f in seen if f["type"] == "state")["state"]
        assert state == "running", "the run was dropped with the socket"

        nodes = {f["id"] for f in seen if f["type"] == "node"}
        assert nodes == {"p", "c"}, "the reconnecting editor was not told the nodes"

        # And it is the same engine, not a fresh one: `tx` still reaches the
        # card the first connection bound.
        socket.send_json({"type": "command", "id": "t", "line": "tx 00B2010C00"})
        result = next(
            f for f in collect(socket, until="result") if f["type"] == "result"
        )
        assert result["lines"][1].startswith("<- 70145A08")

        socket.send_json({"type": "stop"})
        collect_state(socket, "stopped")


def test_stop_still_ends_it(client):
    """The run outlives a socket, not a `stop`."""
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph_frame()})
        collect_state(socket, "running")
        socket.send_json({"type": "stop"})
        collect_state(socket, "stopped")

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        seen = collect(socket, until="state")
        assert next(f for f in seen if f["type"] == "state")["state"] == "stopped"
        assert not [f for f in seen if f["type"] == "node"]


# ── What the frame queue may cost the relay ─────────────────────────────────
#
# `_offer` runs on the thread driving the card. It may not block, may not
# raise, and may not do work proportional to how far behind the listener is.
# The version before this one broke the third: at the limit it drained and
# refilled the whole queue for every non-apdu frame — 6.7 ms each, on the relay
# thread — and grew without limit while doing it, because PROTOCOL.md says node
# and edge frames must not be dropped and it had nothing else to drop.


def a_session() -> "session_module.Session":
    async def nowhere(frame):
        return None

    return session_module.Session(nowhere)


def held(session) -> int:
    return len(session._stream) + len(session._latest) + len(session._states)


def test_a_flood_of_apdus_is_capped_and_counted():
    session = a_session()
    for i in range(session_module.STREAM_LIMIT + 500):
        session._offer({"type": "apdu", "event": {"i": i}})

    assert len(session._stream) == session_module.STREAM_LIMIT
    assert session._dropped == 500
    # The *oldest* went, so the newest trace is what survives.
    assert session._stream[-1]["event"]["i"] == session_module.STREAM_LIMIT + 499


def test_a_flood_of_node_frames_does_not_grow_the_queue():
    """
    The relay emits one node frame per node on every exchange, so this is the
    real shape of the load. Only the newest value of each node means anything,
    so they coalesce by id and the memory is bounded by the graph, not the run.
    """
    session = a_session()
    for i in range(session_module.STREAM_LIMIT):
        session._offer({"type": "apdu", "event": {"i": i}})
    before = held(session)

    for i in range(5000):
        session._offer({"type": "node", "id": "n%d" % (i % 4), "telemetry": {"apdus": i}})

    assert held(session) == before + 4, "one entry per node, however many arrive"
    assert session._dropped == 0, "node frames must not be dropped"
    # And it is the newest telemetry that survives, not the first.
    assert session._latest[("node", "n3")]["telemetry"]["apdus"] == 4999


def test_offering_at_the_limit_is_constant_time():
    """
    Measured, because this is the regression that mattered: 6.7 ms per frame
    on the thread holding a card is a dropped tap.
    """
    session = a_session()
    for i in range(session_module.STREAM_LIMIT):
        session._offer({"type": "apdu", "event": {"i": i}})

    started = time.perf_counter()
    for i in range(2000):
        session._offer({"type": "node", "id": "n%d" % (i % 4), "telemetry": {}})
        session._offer({"type": "apdu", "event": {"i": i}})
    each_ms = (time.perf_counter() - started) / 4000 * 1000

    assert each_ms < 0.1, "%.3f ms per frame on the relay thread" % each_ms


def test_a_state_frame_is_never_dropped():
    session = a_session()
    for i in range(session_module.STREAM_LIMIT * 2):
        session._offer({"type": "apdu", "event": {"i": i}})
    for state in ("arming", "running", "stopping", "stopped"):
        session._offer({"type": "state", "state": state})

    assert [f["state"] for f in session._states] == [
        "arming",
        "running",
        "stopping",
        "stopped",
    ]


def test_a_flush_sends_the_trace_then_the_telemetry_then_the_state():
    """
    A state change is the conclusion of the window it closes, so it reads last;
    node telemetry reflects the end of that window, so it reads after the
    exchanges that produced it.
    """
    import asyncio

    sent = []

    async def capture(frame):
        sent.append(frame)

    session = session_module.Session(capture)
    session._offer({"type": "apdu", "event": {"i": 1}})
    session._offer({"type": "node", "id": "n", "telemetry": {}})
    session._offer({"type": "state", "state": "running"})

    asyncio.get_event_loop().run_until_complete(session.flush())

    assert len(sent) == 1 and sent[0]["type"] == "batch"
    assert [f["type"] for f in sent[0]["frames"]] == ["apdu", "node", "state"]
    assert held(session) == 0, "a flush empties everything it sent"


# ── Every console verb ──────────────────────────────────────────────────────
#
# `help`, `status` and `tx` had tests; `nodes`, `edges`, `devices`, `pause`,
# `resume` and `check` had none, and neither did `release` until the breakpoint
# work. A verb nobody tests is a verb that can quietly stop answering — and
# PROTOCOL.md says these are the ones every runtime should have.


def armed_client(client):
    """A socket with the probe rig armed and settled."""
    socket = client.websocket_connect("/ws").__enter__()
    socket.send_json({"type": "hello", "version": 1})
    collect(socket, until="state")
    socket.send_json({"type": "arm", "graph": graph_frame()})
    collect_state(socket, "running")
    return socket


def answer(socket, line: str):
    socket.send_json({"type": "command", "id": line.split()[0], "line": line})
    return next(f for f in collect(socket, until="result") if f["type"] == "result")


def test_every_verb_the_protocol_lists_is_answered(client):
    """
    The list in PROTOCOL.md, checked against the runtime rather than against a
    copy of the list.
    """
    from plexus.api import console

    documented = {
        "help",
        "status",
        "nodes",
        "edges",
        "devices",
        "tx",
        "pause",
        "resume",
        "check",
        "release",
    }
    assert set(console.verbs()) == documented

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        for verb in sorted(documented):
            result = answer(socket, verb)
            assert result["lines"], "%s answered with nothing" % verb


def test_nodes_and_edges_report_the_armed_chain(client):
    socket = armed_client(client)
    try:
        nodes = "\n".join(answer(socket, "nodes")["lines"])
        assert "p" in nodes and "c" in nodes
        assert "apdu" in nodes

        edges = "\n".join(answer(socket, "edges")["lines"])
        assert "p:card -> c:terminal" in edges
        assert "B out" in edges and "B in" in edges
    finally:
        socket.__exit__(None, None, None)


def test_devices_says_this_build_has_none(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        lines = answer(socket, "devices")["lines"]
        assert any("No devices" in line for line in lines)


def test_check_reruns_the_graph_checks(client):
    socket = armed_client(client)
    try:
        assert answer(socket, "check")["lines"] == ["Nothing to fix."]
    finally:
        socket.__exit__(None, None, None)


def test_check_with_no_graph_says_so(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        assert answer(socket, "check")["lines"] == ["No graph loaded."]


def test_pause_holds_traffic_and_resume_lets_it_go(client):
    socket = armed_client(client)
    try:
        # The probe has run out of script by now, so the state is `running`
        # with a dead driver — `pause` still applies to the chain.
        paused = answer(socket, "pause")
        # `level` is omitted when there is nothing wrong, not set to null.
        assert paused.get("level") != "error", paused["lines"]

        status = "\n".join(answer(socket, "status")["lines"])
        assert "paused" in status

        answer(socket, "resume")
        assert "paused" not in "\n".join(answer(socket, "status")["lines"])
    finally:
        socket.__exit__(None, None, None)


def test_pause_with_nothing_running_is_an_error(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        assert answer(socket, "pause").get("level") == "error"


# ── Batching ────────────────────────────────────────────────────────────────


def test_a_busy_run_is_delivered_as_batches(client):
    """
    PROTOCOL.md: a runtime "should coalesce a tick's worth of `node`, `edge`
    and `apdu` frames into one `batch`". Nothing asserted it, so removing the
    batching entirely would have left every test green — `unwrap` flattens a
    batch, and a stream of single frames unwraps to the same list.
    """
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")

        socket.send_json({"type": "arm", "graph": graph_frame()})

        # Read raw, without unwrapping, until the run has settled.
        raw = []
        deadline = time.monotonic() + FRAME_BUDGET_S
        while time.monotonic() < deadline:
            frame = pull(socket, deadline)
            raw.append(frame)
            if any(
                f.get("type") == "log"
                and "still bound" in f.get("line", {}).get("text", "")
                for f in unwrap(frame)
            ):
                break

        batches = [f for f in raw if f.get("type") == "batch"]
        assert batches, "a whole run arrived without a single batch"
        assert any(len(b["frames"]) > 1 for b in batches), (
            "every batch carried one frame, which is not coalescing"
        )
        # And a batch is worth having: more frames came out than went over.
        assert sum(len(unwrap(f)) for f in raw) > len(raw)


# ── What this build can bind ────────────────────────────────────────────────


def test_hello_says_which_nodes_this_runtime_cannot_bind(client):
    """
    The editor drew every node as if it would run and you found out at Run —
    after the card had been opened, with the reason arriving on the relay
    thread. The runtime has always known the answer; nothing asked for it.
    """
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        seen = collect(socket, until="state")

        frame = next(f for f in seen if f["type"] == "implementations")
        needs = frame["needs"]

        # Every key is a node type, and every value says what it wants.
        assert "pcsc" in needs
        assert "reader" in needs["pcsc"]
        assert "nfcgate" in needs
        # The ones this build really does bind are absent, not present-and-empty.
        for ready in ("probe", "capture", "trace", "mutation", "inject"):
            assert ready not in needs


def test_the_two_answers_about_a_node_cannot_disagree(client):
    """
    `/api/health` reports *whether*, the socket reports *why*, and both come
    from the same tables. A node marked pending with nothing to say, or a
    reason attached to a node that binds fine, would be worse than either.
    """
    health = client.get("/api/health").json()

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        seen = collect(socket, until="state")
        needs = next(f for f in seen if f["type"] == "implementations")["needs"]

    assert health["needs"] == needs
    pending = {key for key, state in health["nodes"].items() if state == "pending"}
    assert set(needs) == pending, "whether and why disagree about some node"
    assert all(reason.strip() for reason in needs.values())


def test_a_node_this_build_cannot_bind_is_refused_with_the_same_sentence(client):
    """
    The palette's reason and the arm's reason are the same string, which is the
    point of taking both from `PENDING`.
    """
    from plexus.runtime import node_requirements

    graph = graph_frame()
    graph["nodes"][1]["type"] = "pcsc"
    graph["nodes"][1]["config"] = {"device": "pcsc:0"}

    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "hello", "version": 1})
        collect(socket, until="state")
        socket.send_json({"type": "arm", "graph": graph})
        error = next(f for f in collect(socket, until="error") if f["type"] == "error")

    assert node_requirements()["pcsc"] in error["message"]

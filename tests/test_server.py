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

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from plexus.api import server as server_module
from plexus.api.server import create_app, host_ok, is_loopback, token_ok
from plexus.api.session import _normalise
from plexus.graph import Graph, GraphError

CAPTURE = Path(__file__).resolve().parent / "captures" / "mastercard-probe.jsonl"
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "probe-a-capture.plexus.yaml"


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


def unwrap(frame: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A batch is a frame carrying frames; everything else is one."""
    if frame.get("type") == "batch":
        out: List[Dict[str, Any]] = []
        for inner in frame["frames"]:
            out.extend(unwrap(inner))
        return out
    return [frame]


def collect(socket, *, until: str, limit: int = 400) -> List[Dict[str, Any]]:
    """Read until a frame of `until` type arrives, flattening batches."""
    seen: List[Dict[str, Any]] = []
    for _ in range(limit):
        for frame in unwrap(socket.receive_json()):
            seen.append(frame)
            if frame.get("type") == until:
                return seen
    raise AssertionError("no %r frame in %d reads: %r" % (until, limit, seen[-5:]))


def collect_state(socket, want: str, limit: int = 400) -> List[Dict[str, Any]]:
    """
    Read until a *particular* state arrives.

    A run passes through several — arming, running, stopping, stopped — so
    waiting for "a state frame" catches whichever came first and tells you
    nothing.
    """
    seen: List[Dict[str, Any]] = []
    for _ in range(limit):
        for frame in unwrap(socket.receive_json()):
            seen.append(frame)
            if frame.get("type") == "state" and frame.get("state") == want:
                return seen
    states = [f.get("state") for f in seen if f.get("type") == "state"]
    raise AssertionError("never reached %r; saw %r" % (want, states))


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


# ── Arming and running ──────────────────────────────────────────────────────


def collect_until(socket, done, limit: int = 400) -> List[Dict[str, Any]]:
    """
    Read until `done(seen)` is true.

    Never wait for something the runtime does not promise: `receive_json`
    blocks forever with nothing to read, so a test that waits for a state the
    runtime will not reach hangs the whole suite rather than failing. It did.
    """
    seen: List[Dict[str, Any]] = []
    for _ in range(limit):
        seen.extend(unwrap(socket.receive_json()))
        if done(seen):
            return seen
    raise AssertionError("condition never met; last frames: %r" % seen[-5:])


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
            {"type": "command", "id": "t", "line": "tx 00A404000E325041592E5359532E444446303100"}
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
    assert [str(e.source) for e in rebuilt.edges] == [str(e.source) for e in from_file.edges]
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
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as socket:
                socket.receive_json()


def test_health_says_what_this_build_can_bind(client):
    body = client.get("/api/health").json()
    assert body["protocol"] == 1
    assert body["nodes"]["capture"] == "ready"
    assert body["nodes"]["pcsc"] == "pending"


_ = (json, os)

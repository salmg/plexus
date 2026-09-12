"""
The things a graph, or a web page, must not be able to do.

Every case here was a real hole that was measured open before it was closed, or
a legitimate use that must keep working. A graph is untrusted input by design —
it arrives over a socket or as a file someone sent you — and it names files,
addresses and hardware.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from plexus.api import console
from plexus.api import server as server_module
from plexus.api.server import MAX_FRAME_BYTES, create_app, origin_ok
from plexus.nodes.card import build as build_card
from plexus.nodes.paths import PathRefused, resolve_read, resolve_write, workspace
from plexus.nodes.transform import MAX_APDU_BYTES, TransformError
from plexus.nodes.transform import build as build_transform
from plexus.runtime import Engine

REPO = Path(__file__).resolve().parent.parent
CAPTURE = "tests/captures/mastercard-probe.jsonl"


@pytest.fixture(autouse=True)
def workspace_is_the_repo(monkeypatch):
    """Every case runs with the workspace where the runtime would really be."""
    monkeypatch.chdir(REPO)
    monkeypatch.delenv("PLEXUS_WORKSPACE", raising=False)


# ── Paths a graph names ─────────────────────────────────────────────────────


def test_a_trace_node_cannot_write_outside_the_workspace():
    """
    Measured open: a graph could append JSON lines to any path the process
    could reach. A small primitive, and a real one.
    """
    outside = Path(tempfile.gettempdir()) / "plexus-must-not-exist.jsonl"
    with pytest.raises(PathRefused) as caught:
        build_transform("trace", {"path": str(outside)})
    assert "outside the workspace" in str(caught.value)
    assert not outside.exists()


def test_traversal_out_of_the_workspace_is_refused():
    for attempt in ("../escape.jsonl", "../../../../escape.jsonl", "logs/../../escape.jsonl"):
        with pytest.raises(PathRefused):
            build_transform("trace", {"path": attempt})


def test_a_trace_node_writes_where_it_should(tmp_path, monkeypatch):
    monkeypatch.setenv("PLEXUS_WORKSPACE", str(tmp_path))
    node = build_transform("trace", {"path": "logs/trace.jsonl"})
    node.on_response(b"\x00\xb2\x01\x0c\x00", b"\x90\x00")
    node.close()

    written = tmp_path / "logs" / "trace.jsonl"
    assert written.is_file()
    assert "00B2010C00" in written.read_text(encoding="utf-8")


def test_a_capture_node_cannot_read_outside_the_workspace():
    with pytest.raises(PathRefused):
        build_card("capture", {"path": "C:/Windows/win.ini"})
    with pytest.raises(PathRefused):
        build_card("capture", {"path": "/etc/passwd"})


def test_a_capture_node_reads_a_capture_inside_the_workspace():
    card = build_card("capture", {"path": CAPTURE, "strict": False})
    assert card.path.is_file()


def test_a_sibling_directory_is_not_inside_the_workspace(tmp_path, monkeypatch):
    """
    A string prefix would accept `/srv/plexus-evil` for a root of `/srv/plexus`.
    Resolved-path containment does not.
    """
    root = tmp_path / "work"
    root.mkdir()
    sibling = tmp_path / "work-evil"
    sibling.mkdir()
    (sibling / "capture.jsonl").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("PLEXUS_WORKSPACE", str(root))
    assert workspace() == root.resolve()
    with pytest.raises(PathRefused):
        resolve_read(str(sibling / "capture.jsonl"))
    with pytest.raises(PathRefused):
        resolve_write(str(sibling / "out.jsonl"))


def test_a_missing_file_is_refused_by_name():
    with pytest.raises(PathRefused) as caught:
        resolve_read("tests/captures/nope.jsonl", what="capture")
    assert "No capture at" in str(caught.value)


# ── Who may open a socket ───────────────────────────────────────────────────


def test_a_page_on_another_origin_cannot_drive_the_runtime():
    """
    The one that mattered. A browser does not apply the same-origin policy to
    WebSockets, and the Host header on such a handshake is honestly
    127.0.0.1 — so a Host check alone let it through. Measured accepted before
    this check existed.
    """
    assert not origin_ok("https://evil.example.com")
    assert not origin_ok("http://evil.example.com:5173")
    assert not origin_ok("null")  # sandboxed iframe or file://
    assert not origin_ok("ws://127.0.0.1:5173")  # not a page origin


def test_the_editor_and_native_clients_are_allowed():
    assert origin_ok("http://localhost:5173")
    assert origin_ok("http://127.0.0.1:5173")
    assert origin_ok("https://localhost:4173")
    assert origin_ok(None), "a native client sends no Origin, and cannot be a browser"
    assert origin_ok("")


def test_an_origin_can_be_allowed_deliberately(monkeypatch):
    assert not origin_ok("https://rig.example.com")
    monkeypatch.setenv(server_module.ORIGINS_VARIABLE, "https://rig.example.com")
    assert origin_ok("https://rig.example.com")
    assert origin_ok("https://rig.example.com/")  # trailing slash tolerated
    assert not origin_ok("https://other.example.com")


def test_a_hostile_origin_is_closed_before_anything_is_accepted():
    with TestClient(create_app()) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws", headers={"origin": "https://evil.example.com"}
            ) as socket:
                socket.receive_json()


def test_the_editors_origin_still_connects():
    with TestClient(create_app()) as client:
        with client.websocket_connect(
            "/ws", headers={"origin": "http://localhost:5173"}
        ) as socket:
            socket.send_json({"type": "hello", "version": 1})
            assert socket.receive_json()["type"] in ("devices", "state", "batch")


# ── Sizes ───────────────────────────────────────────────────────────────────


def test_an_oversized_frame_is_refused_without_being_parsed():
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "version": 1})
            socket.receive_json()

            socket.send_text('{"type":"arm","graph":{"pad":"' + "A" * MAX_FRAME_BYTES + '"}}')
            for _ in range(20):
                frame = socket.receive_json()
                if frame.get("type") == "error" and "limit" in frame.get("message", ""):
                    return
            raise AssertionError("the oversized frame was not refused")


def test_a_frame_that_is_not_json_is_a_message_not_a_crash():
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "version": 1})
            socket.receive_json()
            socket.send_text("not json at all")
            for _ in range(20):
                frame = socket.receive_json()
                if frame.get("type") == "error" and "JSON" in frame.get("message", ""):
                    return
            raise AssertionError("malformed JSON was not reported")


def test_an_apdu_larger_than_a_card_can_read_is_refused_everywhere():
    too_long = "00" * (MAX_APDU_BYTES + 1)

    lines, level = console.run(Engine(), None, "tx " + too_long)
    assert level == "error"
    assert "at most" in lines[0]

    with pytest.raises(TransformError):
        build_transform("inject", {"triggerIns": "AE", "apdu": too_long})


def test_a_normal_apdu_is_not_caught_by_the_cap():
    engine = Engine()
    lines, level = console.run(engine, None, "tx 00B2010C00")
    # Nothing is armed, so it fails for that reason and not for its size.
    assert level == "error"
    assert "at most" not in lines[0]


# ── What a graph cannot smuggle ─────────────────────────────────────────────


def test_a_playbook_cannot_construct_objects():
    """
    A mutation node's playbook is YAML that reaches atrium's loader. That
    loader uses `yaml.safe_load`, so a tag asking for an object is an error
    rather than a call.
    """
    hostile = 'enabled: true\nresponse_mutations: !!python/object/apply:os.system ["echo pwned"]\n'
    with pytest.raises(TransformError):
        build_transform("mutation", {"playbook": hostile})


def test_an_empty_playbook_is_refused_rather_than_silently_doing_nothing():
    with pytest.raises(TransformError):
        build_transform("mutation", {"playbook": "   "})


def test_a_graph_naming_an_unknown_node_type_is_refused():
    from plexus.graph import Graph, GraphError

    with pytest.raises(GraphError) as caught:
        Graph.from_yaml(
            "version: 1\nname: x\nnodes:\n  - id: a\n    type: plexus.evil\n"
        )
    assert "does not know" in str(caught.value)

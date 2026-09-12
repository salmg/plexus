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
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

# The bounded frame collector, not a second copy of it: an unbounded read here
# would hang this suite exactly as it hung that one.
from frames import collect

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
    for attempt in (
        "../escape.jsonl",
        "../../../../escape.jsonl",
        "logs/../../escape.jsonl",
    ):
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
        with pytest.raises(WebSocketDisconnect):
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

            socket.send_text(
                '{"type":"arm","graph":{"pad":"' + "A" * MAX_FRAME_BYTES + '"}}'
            )
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
    hostile = (
        "enabled: true\n"
        'response_mutations: !!python/object/apply:os.system ["echo pwned"]\n'
    )
    with pytest.raises(TransformError):
        build_transform("mutation", {"playbook": hostile})


def test_an_empty_playbook_is_refused_rather_than_silently_doing_nothing():
    with pytest.raises(TransformError):
        build_transform("mutation", {"playbook": "   "})


def test_a_playbook_cannot_write_its_log_outside_the_workspace():
    """
    The hole: atrium takes `log_path` straight out of the playbook document,
    and `MutationLog` opens it at construction — creating parent directories on
    the way. A trace node's `path` went through the workspace guard; this one
    went nowhere near it, so the same arbitrary file write was still available
    one YAML key over.
    """
    escape = Path(tempfile.gettempdir()).resolve() / "plexus-escape" / "mutations.jsonl"
    if escape.exists():
        escape.unlink()

    playbook = "enabled: true\nlog_mutations: true\nlog_path: %s\n" % escape.as_posix()
    with pytest.raises(PathRefused) as refused:
        build_transform("mutation", {"playbook": playbook})

    assert "outside the workspace" in str(refused.value)
    assert not escape.exists(), "the log was created before being refused"
    assert not escape.parent.exists(), "the directory was created anyway"


def test_traversal_in_a_playbook_log_path_is_refused_too():
    playbook = "enabled: true\nlog_path: ../../plexus-escape.jsonl\n"
    with pytest.raises(PathRefused) as refused:
        build_transform("mutation", {"playbook": playbook})
    assert "outside the workspace" in str(refused.value)


def test_a_playbook_log_path_inside_the_workspace_still_works():
    """The legitimate use, which has to keep working."""
    wanted = REPO / "logs" / "playbook-log-test.jsonl"
    if wanted.exists():
        wanted.unlink()
    try:
        node = build_transform(
            "mutation",
            {"playbook": "enabled: true\nlog_path: logs/playbook-log-test.jsonl\n"},
        )
        node.close()
        assert wanted.exists(), "atrium never opened the log it was handed"
    finally:
        if wanted.exists():
            wanted.unlink()


def test_the_playbook_atrium_reloads_is_the_sanitised_one():
    """
    Atrium's `reload()` re-reads the playbook at the start of every
    transaction and takes `log_path` from it again. Writing the operator's text
    verbatim would have left that path an escape even with the first load
    checked, so what lands on disk is the document after the guard.
    """
    import yaml

    node = build_transform(
        "mutation",
        {"playbook": "enabled: true\nlog_path: logs/reload-check.jsonl\n"},
    )
    try:
        on_disk = yaml.safe_load(node._temp.read_text(encoding="utf-8"))
        # Absolute, resolved, and inside the workspace — not what was typed.
        assert Path(on_disk["log_path"]).is_absolute()
        # `relative_to` raises when it is not inside; 3.8 has no is_relative_to.
        Path(on_disk["log_path"]).resolve().relative_to(workspace())
    finally:
        node.close()
        for leftover in (REPO / "logs" / "reload-check.jsonl",):
            if leftover.exists():
                leftover.unlink()


def test_the_log_every_rewrite_switch_reaches_atrium():
    """
    It did not. The node stored `logMutations` on itself and never read it
    again; atrium reads `log_mutations` out of the playbook, so the inspector's
    checkbox did nothing and contradicted the playbook text beside it.
    """
    import yaml

    node = build_transform(
        "mutation",
        {"playbook": "enabled: true\nlog_mutations: true\n", "logMutations": False},
    )
    try:
        on_disk = yaml.safe_load(node._temp.read_text(encoding="utf-8"))
        assert on_disk["log_mutations"] is False
        assert "log_path" not in on_disk
    finally:
        node.close()


def test_a_graph_naming_an_unknown_node_type_is_refused():
    from plexus.graph import Graph, GraphError

    with pytest.raises(GraphError) as caught:
        Graph.from_yaml(
            "version: 1\nname: x\nnodes:\n  - id: a\n    type: plexus.evil\n"
        )
    assert "does not know" in str(caught.value)


# ── The handshake, and what can be sent through it ──────────────────────────


def test_a_non_ascii_token_is_refused_rather_than_crashing(monkeypatch):
    """
    `secrets.compare_digest` on `str` refuses anything outside ASCII, so a
    non-ASCII token raised TypeError *during the handshake* — an unhandled
    exception where a closed socket belonged. Whoever dials chooses the token
    they offer, so whoever dials chose whether the check crashed.
    """
    monkeypatch.setenv(server_module.TOKEN_VARIABLE, "sekrit")
    assert server_module.token_ok("sékrit") is False
    assert server_module.token_ok("\U0001f600") is False
    assert server_module.token_ok("sekrit") is True

    # And with a non-ASCII token configured, the right one still works.
    monkeypatch.setenv(server_module.TOKEN_VARIABLE, "clé-secrète")
    assert server_module.token_ok("clé-secrète") is True
    assert server_module.token_ok("cle-secrete") is False


def test_a_binary_frame_is_answered_rather_than_killing_the_session():
    """
    `receive_text()` does `message["text"]`, so one binary frame raised
    KeyError out of the read loop — "session failed", socket left open with
    nobody reading it.
    """
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "version": 1})
            collect(socket, until="state")

            # Valid JSON, sent as bytes: accepted, because PROTOCOL.md does not
            # say which framing and `send_json(mode="binary")` is legal.
            socket.send_bytes(b'{"type": "command", "id": "b", "line": "help"}')
            result = next(
                f for f in collect(socket, until="result") if f["type"] == "result"
            )
            assert result["id"] == "b"

            # Bytes that are not UTF-8: a message, not a traceback.
            socket.send_bytes(b"\xff\xfe\x00\x01")
            error = next(
                f for f in collect(socket, until="error") if f["type"] == "error"
            )
            assert "UTF-8" in error["message"]

            # Still alive.
            socket.send_json({"type": "command", "id": "after", "line": "help"})
            assert (
                next(
                    f for f in collect(socket, until="result") if f["type"] == "result"
                )["id"]
                == "after"
            )


def test_the_frame_cap_counts_bytes_not_characters():
    """
    The constant is named MAX_FRAME_BYTES and was compared against `len()`,
    which counts characters. One character can be four bytes, so a frame
    inside the limit by that measure could be four times over it.
    """
    # A quarter of the cap in characters, four bytes each: over the limit.
    wide = "\U0001f600" * (MAX_FRAME_BYTES // 4 + 100)
    assert len(wide) < MAX_FRAME_BYTES
    assert len(wide.encode("utf-8")) > MAX_FRAME_BYTES

    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "version": 1})
            collect(socket, until="state")
            socket.send_text('{"type":"arm","graph":{"pad":"' + wide + '"}}')
            error = next(
                f for f in collect(socket, until="error") if f["type"] == "error"
            )
            assert "the limit is" in error["message"]


def test_nothing_can_be_armed_before_hello():
    """
    PROTOCOL.md: the editor "sends `hello` first". Nothing enforced it, so a
    client that had never agreed a protocol version could arm a card relay.
    """
    with TestClient(create_app()) as client:
        with client.websocket_connect("/ws") as socket:
            for frame in (
                {"type": "arm", "graph": {}},
                {"type": "stop"},
                {"type": "command", "id": "x", "line": "help"},
            ):
                socket.send_json(frame)
                error = next(
                    f for f in collect(socket, until="error") if f["type"] == "error"
                )
                assert "hello first" in error["message"], frame["type"]

            # And after hello, the same frames are answered.
            socket.send_json({"type": "hello", "version": 1})
            collect(socket, until="state")
            socket.send_json({"type": "command", "id": "ok", "line": "help"})
            assert (
                next(
                    f for f in collect(socket, until="result") if f["type"] == "result"
                )["id"]
                == "ok"
            )


def test_a_token_in_the_url_is_kept_out_of_the_access_log():
    """
    A browser cannot set a header on a WebSocket, so `?token=…` is the only
    way to authenticate one from a page — and uvicorn logs the path it was
    given, query string and all. The secret then sits in a log file and in
    whatever collects it.
    """
    import logging

    redact = server_module.RedactToken()

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/1.1" %d',
        ("127.0.0.1:1", "GET", "/ws?token=s3cret-value&x=1", 101),
        None,
    )
    assert redact.filter(record) is True
    rendered = record.getMessage()
    assert "s3cret-value" not in rendered
    assert "token=[redacted]" in rendered
    assert "x=1" in rendered, "only the token is redacted"

    # Also when the whole line is already interpolated.
    plain = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1,
        "connection open: /ws?x-plexus-token=other-secret", None, None,
    )
    redact.filter(plain)
    assert "other-secret" not in plain.getMessage()

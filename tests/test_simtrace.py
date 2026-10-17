"""
The SimTrace2 node: finding the board, and relaying a real terminal's commands.

No board and no daemon here, and none in CI. What can be real is everything
from vpcd inwards: a stand-in vpcd on a local socket speaks the same two-byte
framing pcscd's vpcd does, atrium's own `VirtualCard` dials it, and the
commands it sends go through the engine to a recorded capture and back. What is
left for the rig is the half before vpcd (board, simtrace2-remsim, the
terminal), which is the operator's and atrium's, not Plexus's.

Discovery is faked at the one place atrium is reached for it, the module
`plexus.adapters.atrium._simtrace_module` loads: the same answers atrium's
`list_devices` and `find_remsim_processes` give, on a machine that has them.
"""

from __future__ import annotations

import socket
import struct
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest
from conftest import needs_atrium

from plexus.adapters import atrium as adapter
from plexus.adapters.atrium import SimTraceDiscovery, simtrace_discovery
from plexus.graph import Graph
from plexus.nodes import terminal as terminal_nodes
from plexus.nodes.card import CardUnavailable
from plexus.nodes.terminal import TerminalUnavailable
from plexus.runtime import Engine, EngineError, discover_hardware, node_requirements
from plexus.runtime.events import Sink

REPO = Path(__file__).resolve().parent.parent
CAPTURE = Path(__file__).resolve().parent / "captures" / "mastercard-probe.jsonl"
PPSE = bytes.fromhex("00A404000E325041592E5359532E444446303100")

#: One board, shaped like atrium's `list_devices()["devices"]` entries.
BOARD = {
    "usb_path": "1-2.3",
    "manufacturer": "sysmocom - s.f.m.c. GmbH",
    "product": "SimTrace2",
    "vendor_id": "1d50",
    "product_id": "60e3",
}
#: A daemon for it, shaped like `find_remsim_processes()` entries.
DAEMON = {"pid": 4242, "user": "root", "usb_path": "1-2.3", "cmdline": "", "started": 0}
COMMAND = (
    "sudo simtrace2-remsim --usb-vendor 1d50 --usb-product 60e3 "
    "--usb-path 1-2.3 --usb-config 1"
)


def _linux(monkeypatch):
    """This machine is Linux, for the code that asks; nothing else changes."""
    fake = types.SimpleNamespace(
        platform="linux", path=sys.path, executable=sys.executable
    )
    monkeypatch.setattr(adapter, "sys", fake)
    monkeypatch.setattr(terminal_nodes, "sys", fake)


def _atrium_sees(monkeypatch, boards, daemons):
    """atrium's SimTrace2 discovery, answering as it would on some machine."""
    module = types.SimpleNamespace(
        list_devices=lambda: {"ok": True, "devices": [dict(b) for b in boards]},
        find_remsim_processes=lambda: [dict(d) for d in daemons],
        simtrace_command=lambda usb_path="": {"command": COMMAND},
    )
    _linux(monkeypatch)
    monkeypatch.setattr(adapter, "atrium_present", lambda: True)
    monkeypatch.setattr(adapter, "_simtrace_module", lambda: module)


# ── discovery ────────────────────────────────────────────────────────────────


def test_a_board_with_its_daemon_up_is_ready(monkeypatch):
    _atrium_sees(monkeypatch, [BOARD], [DAEMON])
    found = simtrace_discovery()
    assert found.problem == "" and found.needs == ""
    assert [b["usb_path"] for b in found.boards] == ["1-2.3"]
    assert found.boards[0]["daemon"]["pid"] == 4242


def test_a_board_with_no_daemon_says_what_to_start(monkeypatch):
    # The case that prompted this: a board plugged in, and nothing saying the
    # daemon was the missing piece, or that Plexus would never start it.
    _atrium_sees(monkeypatch, [BOARD], [])
    found = simtrace_discovery()
    assert found.boards and found.boards[0]["daemon"] is None
    assert COMMAND in found.problem
    assert "Plexus never launches it" in found.problem


def test_no_board_says_which_usb_id_was_looked_for(monkeypatch):
    _atrium_sees(monkeypatch, [], [])
    found = simtrace_discovery()
    assert found.boards == []
    assert "1d50" in found.problem and "60e3" in found.problem
    # A VM that was not handed the USB device is the usual reason on a laptop.
    assert "VM" in found.problem


def test_a_daemon_for_a_board_this_machine_cannot_see_is_named(monkeypatch):
    _atrium_sees(monkeypatch, [], [dict(DAEMON, usb_path="3-1")])
    assert "running for 3-1" in simtrace_discovery().problem


def test_off_linux_says_where_to_run_it(monkeypatch):
    monkeypatch.setattr(
        adapter, "sys", types.SimpleNamespace(platform="win32", path=sys.path)
    )
    found = simtrace_discovery()
    assert "Linux" in found.problem
    assert found.needs.startswith("Linux")


def test_a_discovery_that_will_not_load_says_so(monkeypatch):
    _linux(monkeypatch)
    monkeypatch.setattr(adapter, "atrium_present", lambda: True)

    def broken():
        raise ImportError("No module named 'fastapi'")

    monkeypatch.setattr(adapter, "_simtrace_module", broken)
    found = simtrace_discovery()
    assert "fastapi" in found.problem and "[runtime]" in found.problem


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /sys and /proc")
@needs_atrium
def test_atriums_own_module_loads_on_its_own():
    # The real file, by path, without atrium's web server: what the fakes
    # above stand in for has to exist with these names.
    module = adapter._simtrace_module()
    assert isinstance(module.list_devices()["devices"], list)
    assert isinstance(module.find_remsim_processes(), list)
    # No USB path: atrium refuses a path naming a board that is not plugged
    # in, and CI has none. The adapter only ever asks for one it just found.
    assert "simtrace2-remsim" in module.simtrace_command()["command"]


# ── what the editor is told ─────────────────────────────────────────────────


def test_a_board_becomes_a_device_and_its_daemon_its_detail(monkeypatch):
    _atrium_sees(monkeypatch, [BOARD], [])
    monkeypatch.setattr(adapter, "pcsc_discovery", lambda: adapter.ReaderDiscovery())
    devices, problems = discover_hardware()
    board = next(d for d in devices if d.kind == "simtrace2")
    assert board.id == "simtrace2:1-2.3"
    assert board.name == "SimTrace2 (USB 1-2.3)"
    # Present is present: "no daemon" is not "plug it in".
    assert board.available is True
    assert board.detail == "no simtrace2-remsim running"
    assert COMMAND in problems["simtrace2"]


def test_the_node_is_pending_only_without_a_board(monkeypatch):
    _atrium_sees(monkeypatch, [], [])
    assert "simtrace2" in node_requirements()

    # A board whose daemon is not up yet is not pending: start it and Run.
    _atrium_sees(monkeypatch, [BOARD], [])
    assert "simtrace2" not in node_requirements()


# ── binding ──────────────────────────────────────────────────────────────────


def test_no_daemon_is_refused_at_bind_with_the_command(monkeypatch):
    _atrium_sees(monkeypatch, [BOARD], [])
    with pytest.raises(TerminalUnavailable) as refused:
        terminal_nodes.build("simtrace2", {"device": "simtrace2:1-2.3"})
    assert COMMAND in str(refused.value)


def test_a_board_that_is_not_plugged_in_is_refused_by_its_usb_path(monkeypatch):
    _atrium_sees(monkeypatch, [BOARD], [DAEMON])
    with pytest.raises(TerminalUnavailable) as refused:
        terminal_nodes.build("simtrace2", {"device": "simtrace2:9-9"})
    assert "9-9 is not plugged in" in str(refused.value)


def test_without_assert_only_it_binds_and_waits_for_the_daemon(monkeypatch):
    _atrium_sees(monkeypatch, [BOARD], [])
    opened = {}
    monkeypatch.setattr(
        adapter,
        "open_virtual_card",
        lambda os_, attack: opened.setdefault("card", types.SimpleNamespace(sock=None)),
    )
    built = terminal_nodes.build(
        "simtrace2", {"device": "simtrace2:1-2.3", "assertOnly": False}
    )
    assert built.usb_path == "1-2.3"
    assert "card" in opened


def test_off_linux_it_is_refused_whatever_the_switch(monkeypatch):
    monkeypatch.setattr(
        terminal_nodes, "sys", types.SimpleNamespace(platform="win32")
    )
    monkeypatch.setattr(
        adapter,
        "simtrace_discovery",
        lambda: SimTraceDiscovery(problem="SimTrace2 runs on Linux.", needs="Linux"),
    )
    with pytest.raises(TerminalUnavailable, match="runs on Linux"):
        terminal_nodes.build("simtrace2", {"assertOnly": False})


# ── the relay, through a stand-in vpcd ──────────────────────────────────────


class StandInVpcd:
    """
    The vpcd end of the socket: what pcscd's vpcd driver does when
    simtrace2-remsim, as a PC/SC client, talks to the "Virtual PCD" reader.

    Frames are a two-byte big-endian length and a payload. A one-byte payload
    is control (0 off, 1 on, 2 reset, 4 ATR); anything longer is a command
    APDU. Both the ATR and an APDU are answered in the same framing.
    """

    def __init__(self) -> None:
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.server.settimeout(5)
        self.port = self.server.getsockname()[1]
        self.conn: socket.socket | None = None

    def accept(self) -> None:
        self.conn, _ = self.server.accept()
        self.conn.settimeout(5)

    def send(self, payload: bytes) -> None:
        assert self.conn is not None
        self.conn.sendall(struct.pack("!H", len(payload)) + payload)

    def answer(self) -> bytes:
        assert self.conn is not None
        size = struct.unpack("!H", self._exactly(2))[0]
        return self._exactly(size)

    def _exactly(self, n: int) -> bytes:
        got = b""
        while len(got) < n:
            chunk = self.conn.recv(n - len(got))
            if not chunk:
                raise ConnectionError("the vpicc closed the socket")
            got += chunk
        return got

    def close(self) -> None:
        for s in (self.conn, self.server):
            if s is not None:
                s.close()


def simtrace_over_capture() -> Graph:
    return Graph.from_yaml(
        textwrap.dedent(
            """
            version: 1
            name: a terminal over a capture
            nodes:
              - id: st
                type: simtrace2
                at: [0, 0]
                config: {device: "simtrace2:1-2.3", assertOnly: true}
              - id: c
                type: capture
                at: [400, 0]
                config: {path: "%s", strict: false}
            edges:
              - from: st:card
                to: c:terminal
            """
            % CAPTURE.as_posix()
        )
    )


@pytest.fixture
def vpcd(monkeypatch):
    """A board with its daemon up, and a stand-in vpcd where atrium dials it."""
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("PLEXUS_WORKSPACE", str(REPO))
    _atrium_sees(monkeypatch, [BOARD], [DAEMON])
    stand_in = StandInVpcd()
    monkeypatch.setattr(adapter, "VPCD_PORT", stand_in.port)
    yield stand_in
    stand_in.close()


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@needs_atrium
def test_a_terminals_commands_reach_the_card_and_come_back(vpcd):
    engine = Engine(sink=Sink())
    engine.arm(simtrace_over_capture())
    try:
        vpcd.accept()
        assert engine.state == "running"

        vpcd.send(b"\x01")  # power on
        vpcd.send(b"\x04")  # what is your ATR
        # The chain's card's own ATR. This capture records none, so it is
        # empty here; a card in a PC/SC reader gives its real one.
        assert vpcd.answer() == bytes(engine._card.get_atr())

        vpcd.send(PPSE)
        response = vpcd.answer()
        # The capture's own answer to the PPSE select, through the chain.
        assert response[:1] == b"\x6f" and response[-2:] == b"\x90\x00"
        assert _wait_for(lambda: engine.telemetry("c").apdus == 1)
    finally:
        started = time.monotonic()
        engine.stop()
        took = time.monotonic() - started

    # Stop got through a read that was blocked on the terminal, which is the
    # part no stop flag alone could do. Promptly, not by the engine giving up
    # on the join after five seconds: on Windows a socket shutdown leaves a
    # blocked recv where it is, and that is what this first did there.
    assert took < 2.0, "stop waited out the join instead of ending the read"
    assert engine.state == "stopped"
    assert engine._thread is None
    assert vpcd.conn.recv(2) == b""  # the vpicc closed its end
    summary = engine._terminal_summary()  # released: nothing left to summarise
    assert summary == []


@needs_atrium
def test_vpcd_going_away_faults_the_run_and_says_why(vpcd):
    sink = Sink()
    engine = Engine(sink=sink)
    engine.arm(simtrace_over_capture())
    try:
        vpcd.accept()
        vpcd.conn.close()
        vpcd.conn = None
        assert _wait_for(lambda: engine.state == "faulted")
        assert "vpcd closed the connection" in (sink.states[-1]["detail"] or "")
    finally:
        engine.stop()


@needs_atrium
def test_a_card_failing_mid_relay_says_so_in_its_own_words(vpcd):
    # The card side failing, a card pulled from its reader mid-transaction,
    # must not be reported as vpcd going away.
    sink = Sink()
    engine = Engine(sink=sink)
    engine.arm(simtrace_over_capture())

    def pulled(apdu):
        raise CardUnavailable("The card was removed from the reader.")

    engine._card.transmit = pulled
    try:
        vpcd.accept()
        vpcd.send(PPSE)
        assert _wait_for(lambda: engine.state == "faulted")
        # The chain names the node it came from; the words are the card's.
        assert sink.states[-1]["detail"] == "c: The card was removed from the reader."
    finally:
        engine.stop()


@needs_atrium
def test_no_vpcd_is_refused_at_arm_before_the_card_opens(monkeypatch):
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("PLEXUS_WORKSPACE", str(REPO))
    _atrium_sees(monkeypatch, [BOARD], [DAEMON])
    # A port nothing listens on: bound and closed again.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    monkeypatch.setattr(adapter, "VPCD_PORT", probe.getsockname()[1])
    probe.close()

    engine = Engine(sink=Sink())
    with pytest.raises(EngineError, match="Could not reach vpcd"):
        engine.arm(simtrace_over_capture())
    assert engine.state == "faulted"
    assert engine._card is None


@needs_atrium
def test_a_card_that_fails_to_open_closes_the_vpcd_socket(vpcd, monkeypatch):
    # Bound first, then the card refuses: the terminal's socket must not be
    # left holding vpcd's slot for the next run to collide with.
    graph = simtrace_over_capture()
    graph.nodes[1].config["path"] = "tests/captures/no-such-capture.jsonl"
    engine = Engine(sink=Sink())
    with pytest.raises(EngineError):
        engine.arm(graph)
    vpcd.accept()
    assert vpcd.conn.recv(2) == b""


def test_stop_reaches_a_relay_that_never_started(monkeypatch):
    closed = threading.Event()

    class Socket:
        def shutdown(self, how):
            pass

        def close(self):
            closed.set()

    _atrium_sees(monkeypatch, [BOARD], [DAEMON])
    terminal = terminal_nodes.SimTrace2Terminal(
        "1-2.3", open_card=lambda os_, attack: types.SimpleNamespace(sock=Socket())
    )
    terminal.close()
    assert closed.is_set()
    # And a drive that arrives after the close returns rather than blocking.
    terminal.drive(channel=None, stop=lambda: False)

"""
Terminal-side nodes — the things that ask.

A terminal owns the loop. The engine hands it a chain that behaves like a card
and then stays out of the way, because that is what a terminal is: the party
that decides when to send and what to send next.

One of these runs without hardware — the fingerprint probe — and it is what
turns "a capture and some transforms" into a graph that actually does
something. The rest present a card to a real terminal, which is not a thing
software can do alone.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from typing import Any, Callable, Dict, List, Optional

from plexus.adapters import atrium as _atrium

# `tlv_walk` and `find_all` live in `tlv.py`: the trace tap decodes the same
# bytes this probe does, and one walker is the only way those two stay
# describing the same card. Imported here rather than moved away from, so every
# existing `from plexus.nodes.terminal import tlv_walk` still reads the same.
from plexus.nodes.tlv import find_all, tlv_walk  # noqa: F401

log = logging.getLogger(__name__)

class TerminalUnavailable(RuntimeError):
    """This terminal node cannot be bound here. User-facing, and says why."""

# ── Reading a card's answers ──────────────────────────────────────────────

SW_OK = b"\x90\x00"

def succeeded(response: bytes) -> bool:
    return len(response) >= 2 and response[-2:] == SW_OK

# ── The probe ───────────────────────────────────────────────────────────────

PPSE = bytes.fromhex("00A404000E325041592E5359532E444446303100")

class FingerprintProbe:
    """
    Runs a discovery sequence and records what the card answered.

    Not a payment: it selects the directory, selects whatever applications the
    card advertises, asks for processing options and reads records. That is the
    part of a transaction that tells you what a card *is*, and it is the part a
    recorded capture can answer honestly.
    """

    DEPTHS = ("quick", "standard", "deep")

    def __init__(self, depth: str = "standard") -> None:
        if depth not in self.DEPTHS:
            raise TerminalUnavailable(
                f"Probe depth is one of {', '.join(self.DEPTHS)}; got {depth!r}."
            )
        self.depth = depth
        self.aids: List[bytes] = []
        self.findings: Dict[str, Any] = {}
        self.sent = 0

    def drive(self, channel, stop: Callable[[], bool]) -> None:
        ask = _asker(channel, stop, self)

        directory = ask(PPSE)
        if directory is None:
            return
        if not succeeded(directory):
            log.info("no PPSE on this card (%s); trying known AIDs", directory[-2:].hex())

        self.aids = find_all(directory, 0x4F) or list(_WELL_KNOWN_AIDS)
        self.findings["aids"] = [a.hex().upper() for a in self.aids]

        for aid in self.aids:
            if stop():
                return
            fci = ask(select_aid(aid))
            if fci is None:
                return
            if not succeeded(fci):
                continue
            self.findings.setdefault("selected", []).append(aid.hex().upper())
            # The application label, when the card gives one. It is the one
            # field that turns a hex string into something a person reads —
            # and it is not sensitive, which the PAN in the same records is.
            label = find_all(fci, 0x50)
            if label:
                self.findings.setdefault("labels", {})[aid.hex().upper()] = (
                    label[0].decode("latin-1", "replace")
                )

            if self.depth == "quick":
                continue

            options = ask(gpo_for(fci))
            if options is None:
                return
            if succeeded(options):
                self.findings["records"] = self.findings.get("records", 0)
                for command in read_records(options, deep=self.depth == "deep"):
                    if stop():
                        return
                    record = ask(command)
                    if record is None:
                        return
                    if succeeded(record):
                        self.findings["records"] += 1

    def note(self, command: bytes, response: bytes) -> None:
        self.sent += 1

    def summary(self) -> List[str]:
        """
        What the card turned out to be, in the words a person would use.

        This is the reason the node exists, and for a while nobody could see
        it: `findings`, `aids` and `sent` were all filled in and read by
        nothing at all. The engine asks for this when the probe finishes.

        Deliberately no PAN, expiry or cardholder name. Those cross the wire
        and go in the trace if you asked for one; a summary printed by default
        is a different decision.
        """
        if not self.findings:
            return []

        aids = self.findings.get("aids") or []
        selected = self.findings.get("selected") or []
        labels = self.findings.get("labels") or {}

        lines = ["%s probe: %d commands sent." % (self.depth, self.sent)]
        if aids:
            lines.append(
                "Advertised %d application%s: %s"
                % (len(aids), "" if len(aids) == 1 else "s", ", ".join(aids))
            )
        if selected:
            lines.append(
                "Selected: %s"
                % ", ".join(
                    "%s (%s)" % (aid, labels[aid]) if aid in labels else aid
                    for aid in selected
                )
            )
        else:
            lines.append("Nothing selected: the card refused every AID it offered.")
        if "records" in self.findings:
            lines.append("Read %d record(s)." % self.findings["records"])
        return lines

def _asker(channel, stop, probe: "FingerprintProbe"):
    """One place that sends, counts, and honours the stop flag."""

    def ask(command: bytes) -> Optional[bytes]:
        if stop():
            return None
        response = channel.transmit(command)
        probe.note(command, response)
        return response

    return ask

#: Tried only when a card has no PPSE — contact cards often do not.
_WELL_KNOWN_AIDS = (
    bytes.fromhex("A0000000031010"),  # Visa
    bytes.fromhex("A0000000041010"),  # Mastercard
    bytes.fromhex("A0000000250109"),  # Amex
)

def select_aid(aid: bytes) -> bytes:
    return bytes([0x00, 0xA4, 0x04, 0x00, len(aid)]) + aid + b"\x00"

def gpo_for(fci: bytes) -> bytes:
    """
    Build GET PROCESSING OPTIONS for whatever PDOL the card asked for.

    Every requested element is sent as zeroes of the right length. A probe is
    not trying to be approved — it is trying to get a well-formed answer, and
    zeroes are the least surprising thing to send.
    """
    pdol = next(iter(find_all(fci, 0x9F38)), b"")
    filler = bytes(_pdol_length(pdol))
    data = b"\x83" + bytes([len(filler)]) + filler
    return bytes([0x80, 0xA8, 0x00, 0x00, len(data)]) + data + b"\x00"

def _pdol_length(pdol: bytes) -> int:
    """Total bytes a PDOL asks for. A DOL is tag/length pairs with no values."""
    total = 0
    i = 0
    while i < len(pdol):
        first = pdol[i]
        i += 1
        if first & 0x1F == 0x1F:
            while i < len(pdol) and pdol[i] & 0x80:
                i += 1
            i += 1
        if i >= len(pdol):
            break
        total += pdol[i]
        i += 1
    return total

def read_records(gpo_response: bytes, deep: bool = False) -> List[bytes]:
    """
    READ RECORD commands for the AFL the card returned.

    The AFL (tag 94) is four-byte entries: SFI in the top five bits, then first
    record, last record, and how many are signed. Without one there is nothing
    to read, and a sweep is only worth it at deep.
    """
    commands: List[bytes] = []
    afl = next(iter(find_all(gpo_response, 0x94)), b"")

    for i in range(0, len(afl) - 3, 4):
        sfi = afl[i] >> 3
        first, last = afl[i + 1], afl[i + 2]
        if sfi == 0 or first == 0 or last < first:
            continue
        for record in range(first, min(last, first + 15) + 1):
            commands.append(bytes([0x00, 0xB2, record, (sfi << 3) | 0x04, 0x00]))

    if deep and not commands:
        for sfi in range(1, 11):
            for record in range(1, 5):
                commands.append(bytes([0x00, 0xB2, record, (sfi << 3) | 0x04, 0x00]))

    return commands

# ── The EMV agent ─────────────────────────────────────────────────────────────
#
# A terminal whose next command is decided by a language model rather than a
# fixed script. It is atrium's agent idea, narrowed to the one role a Plexus
# node fills: the party that asks. The model gets one action — send an APDU —
# and the card's own reply straight back, so it drives the conversation the way
# a terminal does, reasoning about each answer instead of following a script.

#: When the node's task field is left blank.
_DEFAULT_TASK = "Identify this card: its applications and what each supports."

_AGENT_SYSTEM = (
    "You are driving a smartcard the way a terminal does, for authorised EMV "
    "security research on a card the operator owns or has written permission to "
    "test. You have one action: send an APDU and read the reply. Work one "
    "command at a time and reason about each response before the next.\n\n"
    "Standard EMV discovery is: SELECT PPSE (2PAY.SYS.DDF01), SELECT each AID it "
    "lists, GET PROCESSING OPTIONS, then READ RECORD across the AFL. Call finish "
    "when the task is done or the card stops answering usefully."
)

_AGENT_TOOLS = [
    {
        "name": "send_apdu",
        "description": (
            "Send one command APDU to the card and get its response. `apdu` is a "
            "hex string, e.g. 00A404000E325041592E5359532E444446303100. The reply "
            "gives the full response hex, the status word, and whether it was 9000."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "apdu": {"type": "string", "description": "Command APDU as hex."},
            },
            "required": ["apdu"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Call when the task is complete. Give a short, plain-language summary "
            "of what the card is and what you found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What you concluded."},
            },
            "required": [],
        },
    },
]


class EmvAgentTerminal:
    """
    A synthetic terminal driven by a language model.

    A terminal owns the loop, so this one does too: `drive` calls the model,
    runs whatever `send_apdu` it asks for against the chain, feeds the answers
    back, and repeats until the model calls `finish`, the card is stopped, or a
    ceiling is hit. The ceilings are the point — a model that will not stop still
    stops here, at a bounded number of turns and of commands.
    """

    #: Model turns, and total commands, before `drive` returns regardless.
    MAX_STEPS = 40
    MAX_COMMANDS = 120

    def __init__(self, config: Dict[str, Any], provider: Any) -> None:
        self.task = str(config.get("task") or "").strip() or _DEFAULT_TASK
        self.brute_sfi = bool(config.get("bruteSfi"))
        self._provider = provider
        self.sent = 0
        self._notes: List[str] = []

    def drive(self, channel, stop: Callable[[], bool]) -> None:
        self._provider.begin(_AGENT_SYSTEM, self._opening())

        for _ in range(self.MAX_STEPS):
            if stop():
                return
            turn = self._provider.complete(_AGENT_TOOLS)
            if getattr(turn, "text", "").strip():
                self._notes.append(turn.text.strip())
            if not turn.wants_tools:
                return

            results = []
            for call in turn.tool_calls:
                if stop():
                    return
                if call.name == "finish":
                    note = str(call.input.get("summary", "")).strip()
                    if note:
                        self._notes.append(note)
                    return
                results.append((call.id, self._send(call, channel)))
                if self.sent >= self.MAX_COMMANDS:
                    self._notes.append(
                        "Reached the %d-command ceiling; stopping." % self.MAX_COMMANDS
                    )
                    return
            self._provider.add_tool_results(results)

        self._notes.append("Reached the %d-turn ceiling; stopping." % self.MAX_STEPS)

    def _send(self, call: Any, channel) -> Dict[str, Any]:
        if call.name != "send_apdu":
            return {"error": "unknown tool %r" % call.name}
        raw = str(call.input.get("apdu", "")).replace(" ", "")
        try:
            command = bytes.fromhex(raw)
        except ValueError:
            return {"error": "apdu was not valid hex: %r" % raw}
        if not command:
            return {"error": "apdu was empty"}

        response = channel.transmit(command)
        self.sent += 1
        return {
            "response": response.hex().upper(),
            "sw": response[-2:].hex().upper() if len(response) >= 2 else "",
            "ok": succeeded(response),
        }

    def _opening(self) -> str:
        extra = (
            " Sweep the SFI range even where the AFL does not mention it."
            if self.brute_sfi
            else ""
        )
        return "Task: %s.%s" % (self.task, extra)

    def summary(self) -> List[str]:
        plural = "" if self.sent == 1 else "s"
        lines = ["EMV agent: %d command%s sent." % (self.sent, plural)]
        lines.extend(self._notes)
        return lines


# ── SimTrace2 ───────────────────────────────────────────────────────────────

#: What a terminal is told when it asks after the relay has been stopped.
#: 6F00, "no precise diagnosis": the honest status for a card that has gone.
_SW_GONE = b"\x6f\x00"


class SimTrace2Terminal:
    """
    A real payment terminal, reached through a SimTrace2 board.

    The terminal is the one asking, so this node does not run a script the way
    the probe does: it hands each command the terminal sends to the chain and
    hands the answer back. The path a command takes to get here is
    terminal → SimTrace2 → simtrace2-remsim → vpcd's "Virtual PCD" reader →
    atrium's ``VirtualCard`` on port 35963, which is what this wraps. The
    daemon is the operator's to start; Plexus only checks it is there.

    Built at bind, connected at bind: vpcd being absent is refused before the
    card is opened, like every other terminal that cannot run. ``close`` is how
    Stop gets through, because ``VirtualCard.run`` blocks on a socket read that
    no stop flag can reach.
    """

    def __init__(
        self, usb_path: str, open_card: Optional[Callable[..., Any]] = None
    ) -> None:
        self.usb_path = usb_path
        self.relayed = 0
        self.power_ups = 0
        self._channel: Any = None
        self._stop: Callable[[], bool] = lambda: False
        self._closed = False
        #: What the chain raised, if it did: a card failing mid-exchange says
        #: so in its own words rather than as vpcd going away.
        self._failure: Optional[BaseException] = None
        opener = open_card or _atrium.open_virtual_card
        try:
            self._card = opener(_ChainOS(self), _ChainAttack(self))
        except _atrium.AtriumUnavailable as exc:
            raise TerminalUnavailable(str(exc)) from exc

    def drive(self, channel, stop: Callable[[], bool]) -> None:
        self._channel, self._stop = channel, stop
        if self._closed or stop():
            self._close_socket()
            return
        gone = TerminalUnavailable(
            "vpcd closed the connection, so the terminal's side of the relay "
            "is gone. Check that pcscd is still running, then Run again."
        )
        try:
            self._card.run()
        except SystemExit:
            # `VirtualCard.run` ends with sys.exit() when vpcd goes away, which
            # on Linux includes the shutdown `close` causes.
            pass
        except Exception as exc:
            # A read on a socket that was closed under it fails inside atrium's
            # loop in more than one way, none of them a message. After a stop
            # that is the stop; a failure the chain raised is its own story;
            # anything else is vpcd gone, said plainly.
            if stop():
                return
            if exc is self._failure:
                raise
            raise gone from exc
        finally:
            self._close_socket()
        if not stop():
            raise gone

    def relay(self, command: bytes) -> bytes:
        """One command from the terminal, through the chain, and its answer."""
        if self._stop() or self._channel is None:
            return _SW_GONE
        self.relayed += 1
        try:
            return bytes(self._channel.transmit(bytes(command)))
        except Exception as exc:
            self._failure = exc
            raise

    def atr(self) -> bytes:
        """The card's ATR, which is what the terminal is shown at power-up."""
        if self._channel is None:
            return b""
        return bytes(self._channel.get_atr())

    def close(self) -> None:
        """Let a blocked `run` return. Safe to call twice and from any thread."""
        if self._closed:
            return
        self._closed = True
        sock = getattr(self._card, "sock", None)
        if sock is None:
            return
        try:
            # Both, because neither alone wakes a blocked recv everywhere: on
            # Linux the shutdown does it and the read returns empty; on Windows
            # a shutdown leaves the read blocked and only the close ends it,
            # with an error `drive` treats as the stop it is. Measured on both.
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._close_socket()

    def _close_socket(self) -> None:
        self._closed = True
        sock = getattr(self._card, "sock", None)
        if sock is not None:
            try:
                sock.close()
            except OSError:  # pragma: no cover - already gone
                pass

    def summary(self) -> List[str]:
        return [
            "SimTrace2 at %s: %d commands from the terminal relayed, %d power-ups."
            % (self.usb_path or "its USB port", self.relayed, self.power_ups)
        ]


class _ChainOS:
    """
    The ``os`` atrium's ``VirtualCard`` asks for the ATR and tells about power.

    A power cycle from the terminal is counted and not passed on: the card is
    held open by the chain for the whole run, and the chain has no reset. A
    terminal that relies on a cold reset between transactions sees a card that
    kept its state. See the caveat on the node.
    """

    def __init__(self, terminal: SimTrace2Terminal) -> None:
        self._terminal = terminal

    def getATR(self) -> bytes:  # noqa: N802 — atrium's name
        return self._terminal.atr()

    def powerUp(self) -> None:  # noqa: N802
        self._terminal.power_ups += 1

    def powerDown(self) -> None:  # noqa: N802
        pass

    def reset(self) -> None:
        pass


class _ChainAttack:
    """The ``attack`` atrium's ``VirtualCard`` hands each command APDU to."""

    def __init__(self, terminal: SimTrace2Terminal) -> None:
        self._terminal = terminal

    def user_execute(self, msg: bytes) -> bytes:
        return self._terminal.relay(bytes(msg))


def _usb_path(device: Any) -> str:
    """``simtrace2:1-2.3`` from the picker, or a bare USB path, to ``1-2.3``."""
    text = str(device or "").strip()
    return text.split(":", 1)[1] if text.startswith("simtrace2:") else text


def simtrace_pending_reason() -> Optional[str]:
    """
    Why a SimTrace2 node cannot bind here at all, or None when it might.

    Pending means no board to bind: not Linux, no atrium, nothing on USB. A
    board whose daemon is not up yet is *not* pending: the operator starts it
    and runs again, and with "Refuse to start if the daemon is not already up"
    switched off the node binds anyway and waits for it.
    """
    found = _atrium.simtrace_discovery()
    if found.boards:
        return None
    return found.needs or "a SimTrace2 on USB with simtrace2-remsim running for it"


def _build_simtrace2(config: Dict[str, Any]) -> SimTrace2Terminal:
    found = _atrium.simtrace_discovery()
    usb = _usb_path(config.get("device"))
    strict = config.get("assertOnly", True) is not False

    if not sys.platform.startswith("linux"):
        raise TerminalUnavailable(found.problem)

    board = next((b for b in found.boards if b.get("usb_path") == usb), None)
    if board is None and not usb and len(found.boards) == 1:
        board = found.boards[0]

    if strict:
        if board is None:
            if usb:
                raise TerminalUnavailable(
                    "The SimTrace2 at USB %s is not plugged in here. %s"
                    % (usb, found.problem)
                )
            raise TerminalUnavailable(
                found.problem or "Pick which SimTrace2 this node is, in the inspector."
            )
        if board.get("daemon") is None:
            raise TerminalUnavailable(
                found.problem
                or "The SimTrace2 at %s has no simtrace2-remsim running for it. "
                "Start it in a root shell, then Run again." % board.get("usb_path")
            )

    return SimTrace2Terminal(usb or (board or {}).get("usb_path", ""))


# ── The rest ────────────────────────────────────────────────────────────────

#: Terminal nodes this build cannot bind at all — they need hardware or a daemon.
#: The agent is *not* here: its code exists, and whether it binds depends on
#: whether a model is configured, which `pending()` decides at the moment asked.
#: Nor is the SimTrace2, for the same reason: whether a board is plugged in.
PENDING = {
    "acr122u.target": "pyscard, an ACR122U, and atrium's PN532 emulator",
    "vpcd": "virtualsmartcard, with vpcd loaded into pcscd",
}

#: Environment that means *some* model back end is configured. Any one will do;
#: atrium works out which and says exactly what is missing if the guess is wrong.
_AGENT_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ATRIUM_LLM_BASE_URL",
    "ATRIUM_LLM_PROVIDER",
)


def agent_pending_reason() -> Optional[str]:
    """Why the agent cannot bind here, or None when it can."""
    if not _atrium.atrium_present():
        return "atrium's agent loop and a model back end"
    if not any(os.environ.get(v) for v in _AGENT_ENV):
        return (
            "a model back end — set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "or ATRIUM_LLM_BASE_URL"
        )
    return None


def pending() -> Dict[str, str]:
    """
    The static hardware gaps, plus the agent when no model is configured.

    A function rather than the `PENDING` constant, because the agent's answer
    depends on the environment the runtime is in, not on how the build was made.
    """
    out = dict(PENDING)
    reason = agent_pending_reason()
    if reason is not None:
        out["agent"] = reason
    reason = simtrace_pending_reason()
    if reason is not None:
        out["simtrace2"] = reason
    return out


def build(node_type: str, config: Dict[str, Any]):
    """
    Make the terminal-side node a catalogue key names.

    A terminal this build cannot drive is refused *here*, not when the relay
    thread first calls `drive`. It used to return a stand-in that raised on the
    worker thread: `arm` reported "running", the editor showed a run in
    progress, and a moment later the state went to faulted with the card
    already open. The card side has always refused at bind, because its
    stand-in raises from `connect`; this is the same answer at the same moment.
    """
    if node_type == "probe":
        return FingerprintProbe(depth=str(config.get("depth") or "standard"))
    if node_type == "agent":
        # Resolving the provider validates the key and the SDK, so a missing
        # model is refused at bind with atrium's own actionable message rather
        # than faulting on the worker thread mid-conversation.
        try:
            provider = _atrium.resolve_llm_provider(str(config.get("provider") or ""))
        except _atrium.AtriumUnavailable as exc:
            raise TerminalUnavailable(str(exc)) from exc
        return EmvAgentTerminal(config, provider)
    if node_type == "simtrace2":
        return _build_simtrace2(config)
    if node_type in PENDING:
        raise TerminalUnavailable(
            "The %s node is not implemented in this build. It needs %s."
            % (node_type, PENDING[node_type])
        )
    raise TerminalUnavailable(f"No terminal implementation for {node_type!r}.")

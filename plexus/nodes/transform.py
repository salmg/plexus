"""
Transforms — the nodes that sit in the conversation.

All of these are pure Python and none of them touch hardware, so every one is
exercised by the test suite. The mutation node is the interesting one: it does
not reimplement anything, it hands the exchange to atrium's own engine.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from plexus.adapters import load_mutation_engine
from plexus.nodes.paths import resolve_write

log = logging.getLogger(__name__)

#: An extended APDU tops out around 65 kB. Anything past that is not a command
#: a card will read, so it is refused before it reaches one.
MAX_APDU_BYTES = 65544


class TransformError(RuntimeError):
    """A transform could not be built or could not run. User-facing."""


class PassThrough:
    """Base for transforms that observe without rewriting."""

    def on_command(self, apdu: bytes) -> bytes:
        return apdu

    def on_response(self, command: bytes, response: bytes) -> bytes:
        return response

    def close(self) -> None:
        """Release anything held. Called even when a run faults."""


class Trace(PassThrough):
    """
    Records the exchange and changes nothing.

    The file is opened once and flushed per line: a trace that is buffered when
    the process is killed is a trace of the part you did not need.
    """

    def __init__(self, path: Optional[str] = None, detail: str = "tlv") -> None:
        self.detail = detail
        self.seen = 0
        self._handle = None
        self.path: Optional[Path] = None
        if path:
            # A graph is untrusted input that names files. Before this was
            # confined, `path` was an arbitrary file write — one appended JSON
            # line at a time, anywhere the process could reach.
            self.path = resolve_write(path, what="trace")
            self._handle = self.path.open("a", encoding="utf-8")

    def on_response(self, command: bytes, response: bytes) -> bytes:
        self.seen += 1
        if self._handle is not None:
            self._handle.write(
                json.dumps(
                    {
                        "cmd": command.hex().upper(),
                        "resp": response.hex().upper(),
                    }
                )
                + "\n"
            )
            self._handle.flush()
        return response

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class Mutation:
    """
    Atrium's mutation engine, as a node.

    The playbook is config on the node rather than a path, because the graph is
    the document — a rig you hand someone should not depend on a YAML file
    beside it that they do not have. It is written to a temporary file because
    ``MutationEngine.from_config`` reads a path, and that is atrium's interface
    rather than something to work around.
    """

    def __init__(
        self,
        playbook: str,
        *,
        card_execute: Optional[Callable[[bytes], bytes]] = None,
        log_mutations: bool = True,
    ) -> None:
        if not playbook or not playbook.strip():
            raise TransformError("This mutation node has an empty playbook.")

        # Parsed here before atrium sees it, because atrium's loader catches a
        # bad document, logs a warning and carries on with defaults. That is
        # the worst outcome available: the node binds, the run starts, and the
        # operator believes mutations are armed when nothing is. A playbook
        # that does not parse has to stop the arm.
        try:
            parsed = yaml.safe_load(playbook)
        except yaml.YAMLError as exc:
            raise TransformError("The playbook is not valid YAML: %s" % exc) from exc
        if not isinstance(parsed, dict):
            raise TransformError(
                "A playbook is a mapping of rules; this one is %s."
                % type(parsed).__name__
            )

        self._temp = Path(
            tempfile.mkstemp(prefix="plexus-playbook-", suffix=".yaml")[1]
        )
        self._temp.write_text(playbook, encoding="utf-8")
        self.log_mutations = log_mutations

        try:
            self._engine = load_mutation_engine(self._temp, card_execute=card_execute)
        except Exception as exc:
            self._cleanup()
            raise TransformError(f"The playbook was not accepted: {exc}") from exc

    def on_command(self, apdu: bytes) -> bytes:
        return self._engine.on_command(apdu)

    def on_response(self, command: bytes, response: bytes) -> bytes:
        return self._engine.on_response(command, response)

    def close(self) -> None:
        closer = getattr(self._engine, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:  # pragma: no cover - best effort on shutdown
                log.debug("mutation engine close failed", exc_info=True)
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            self._temp.unlink()
        except OSError:
            pass


class Inject(PassThrough):
    """
    Fires an extra command at the card around a trigger instruction.

    The injected command goes down the out-of-band line, which reaches the card
    past everything downstream of this node. That is the dashed edge on the
    canvas, and it is drawn rather than hidden because it spends the round trip
    twice — free on contact, and enough to time a contactless tap out.
    """

    def __init__(
        self,
        trigger_ins: str,
        apdu: str,
        *,
        when: str = "after_response",
        repeat: bool = False,
        card_execute: Optional[Callable[[bytes], bytes]] = None,
    ) -> None:
        self.trigger = _one_byte(trigger_ins, "trigger INS")
        self.payload = _hex(apdu, "APDU to inject")
        self.when = when
        self.repeat = repeat
        self._execute = card_execute
        self.fired = 0
        self.results: List[bytes] = []
        self._spent = False

    def on_command(self, apdu: bytes) -> bytes:
        if self.when == "before_command" and self._should_fire(apdu):
            self._fire()
        return apdu

    def on_response(self, command: bytes, response: bytes) -> bytes:
        if self.when == "after_response" and self._should_fire(command):
            self._fire()
        return response

    def _should_fire(self, command: bytes) -> bool:
        if self._spent and not self.repeat:
            return False
        return len(command) >= 2 and command[1] == self.trigger

    def _fire(self) -> None:
        self._spent = True
        if self._execute is None:
            # No card bound yet. Count it so the miss is visible rather than
            # looking like the trigger never matched.
            log.warning("injection has no out-of-band line to the card; skipped")
            return
        self.fired += 1
        self.results.append(self._execute(self.payload))


class Swtx(PassThrough):
    """
    Asks the terminal for more time when the card is slow.

    On the wire this is an S(WTX) block, which only the node that owns the RF
    or contact interface can actually send — so here it only decides *when* one
    is warranted and records it. The terminal-side node reads `pending` and
    sends it. Against a capture there is no terminal to ask, so this counts and
    nothing more.
    """

    def __init__(self, wtxm: int = 4, threshold_ms: int = 200) -> None:
        if not 1 <= wtxm <= 59:
            raise TransformError("A WTX multiplier is between 1 and 59.")
        self.wtxm = wtxm
        self.threshold_ms = threshold_ms
        self.requested = 0
        self.pending = 0

    def note_elapsed(self, elapsed_ms: float) -> bool:
        """Called by the engine after each exchange. True when time was asked for."""
        if elapsed_ms < self.threshold_ms:
            return False
        self.requested += 1
        self.pending += 1
        return True


class Breakpoint:
    """
    Holds each matching command until it is released.

    This stops the clock dead, and any real terminal will time out while you
    think — which is the point of it being a node you add deliberately rather
    than a mode. Against a capture there is no clock to stop.
    """

    def __init__(self, match_ins: str = "", editable: bool = True) -> None:
        self.match = _one_byte(match_ins, "INS to break on") if match_ins else None
        self.editable = editable
        self.held: Optional[bytes] = None
        self.replacement: Optional[bytes] = None
        self.hits = 0
        self._released = threading.Event()
        self._released.set()
        self.enabled = True

    def on_command(self, apdu: bytes) -> bytes:
        if not self.enabled or (self.match is not None and (len(apdu) < 2 or apdu[1] != self.match)):
            return apdu

        self.hits += 1
        self.held = apdu
        self.replacement = None
        self._released.clear()
        self._released.wait()

        held = self.replacement if (self.editable and self.replacement) else apdu
        self.held = None
        self.replacement = None
        return held

    def on_response(self, command: bytes, response: bytes) -> bytes:
        return response

    def release(self, replacement: Optional[bytes] = None) -> None:
        """Let the held command go, optionally with different bytes."""
        self.replacement = replacement
        self._released.set()

    def close(self) -> None:
        # Never leave a driver blocked on a breakpoint that will not return.
        self.enabled = False
        self._released.set()


def _hex(text: str, what: str) -> bytes:
    cleaned = "".join(text.split()).replace(":", "")
    if not cleaned:
        raise TransformError(f"{what} is empty.")
    if len(cleaned) > MAX_APDU_BYTES * 2:
        raise TransformError(
            f"{what} is {len(cleaned) // 2} bytes; an APDU is at most {MAX_APDU_BYTES}."
        )
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise TransformError(f"{what} is not hex: {text!r}") from exc


def _one_byte(text: str, what: str) -> int:
    raw = _hex(text, what)
    if len(raw) != 1:
        raise TransformError(f"{what} is one byte, got {len(raw)}.")
    return raw[0]


def build(node_type: str, config: Dict[str, Any], *, card_execute=None):
    """Make the transform a catalogue key names."""
    if node_type == "trace":
        return Trace(path=config.get("path") or None, detail=config.get("detail", "tlv"))
    if node_type == "mutation":
        return Mutation(
            str(config.get("playbook") or ""),
            card_execute=card_execute,
            log_mutations=bool(config.get("logMutations", True)),
        )
    if node_type == "inject":
        return Inject(
            str(config.get("triggerIns") or ""),
            str(config.get("apdu") or ""),
            when=str(config.get("when") or "after_response"),
            repeat=bool(config.get("repeat", False)),
            card_execute=card_execute,
        )
    if node_type == "swtx":
        return Swtx(
            wtxm=int(config.get("wtxm", 4)),
            threshold_ms=int(config.get("thresholdMs", 200)),
        )
    if node_type == "breakpoint":
        return Breakpoint(
            match_ins=str(config.get("matchIns") or ""),
            editable=bool(config.get("editable", True)),
        )
    raise TransformError(f"No transform implementation for {node_type!r}.")

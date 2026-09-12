"""
Transforms — the nodes that sit in the conversation.

All of these are pure Python and none of them touch hardware, so every one is
exercised by the test suite. The mutation node is the interesting one: it does
not reimplement anything, it hands the exchange to atrium's own engine.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from plexus.adapters import load_mutation_engine
from plexus.nodes.paths import resolve_write
from plexus.nodes.tlv import decode as tlv_decode
from plexus.nodes.tlv import status_word

log = logging.getLogger(__name__)

#: An extended APDU tops out around 65 kB. Anything past that is not a command
#: a card will read, so it is refused before it reaches one.
MAX_APDU_BYTES = 65544

#: Where a mutation log goes when the playbook does not say. Atrium's own
#: default, stated here so the file lands somewhere known inside the workspace
#: rather than wherever the process happens to be standing.
DEFAULT_MUTATION_LOG = "logs/mutations.jsonl"


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

    `detail` decides how much goes in each line:

        raw        the two hex strings, and nothing else
        tlv        plus every TLV in the response, flattened
        annotated  plus the name of each tag this build knows, and what the
                   status word means

    It was stored and never read — every trace was `raw`, whatever the
    inspector said, while the node's own summary and docs/nodes.md both claimed
    it decoded TLV.
    """

    DETAILS = ("raw", "tlv", "annotated")

    def __init__(self, path: Optional[str] = None, detail: str = "tlv") -> None:
        if detail not in self.DETAILS:
            raise TransformError(
                "Detail is one of %s; this one says %r."
                % (", ".join(self.DETAILS), detail)
            )
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

    def line(self, command: bytes, response: bytes) -> Dict[str, Any]:
        """One trace entry. Separate from writing it, so it can be tested."""
        entry: Dict[str, Any] = {
            "cmd": command.hex().upper(),
            "resp": response.hex().upper(),
        }
        if self.detail == "raw":
            return entry

        named = self.detail == "annotated"
        # The status word is not part of the TLV, so it is decoded separately
        # and the body is what is left.
        body = response[:-2] if len(response) >= 2 else b""
        decoded = tlv_decode(body, named=named)
        if decoded:
            entry["tlv"] = decoded
        if named:
            status = status_word(response)
            if status:
                entry["status"] = status
        return entry

    def on_response(self, command: bytes, response: bytes) -> bytes:
        self.seen += 1
        if self._handle is not None:
            self._handle.write(json.dumps(self.line(command, response)) + "\n")
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

        # The node's own "Log every rewrite" switch, written into the document
        # rather than stored and forgotten. It was the latter: atrium reads
        # `log_mutations` out of the playbook, so the checkbox in the inspector
        # did nothing at all, and disagreed with the `log_mutations: true` in
        # the default playbook text sitting beside it.
        parsed["log_mutations"] = bool(log_mutations)

        if log_mutations:
            # `log_path` is a file write a graph asks for, so it goes through
            # the same guard as a trace node's path. Atrium's `MutationLog`
            # opens it at construction and creates its parent directories, so
            # unchecked it was an arbitrary write anywhere the process could
            # reach — outside the workspace included.
            raw_log = parsed.get("log_path") or DEFAULT_MUTATION_LOG
            if not isinstance(raw_log, str) or not raw_log.strip():
                raise TransformError(
                    "A playbook's log_path must be a path; this one is %s."
                    % type(parsed.get("log_path")).__name__
                )
            parsed["log_path"] = str(resolve_write(raw_log, what="mutation log"))
        else:
            parsed.pop("log_path", None)

        # `mkstemp` returns an open descriptor *and* a path. Taking `[1]` and
        # dropping the descriptor leaked it for the life of the process — and
        # on Windows an open handle is also why `_cleanup`'s unlink quietly
        # failed, so every mutation node ever armed left its playbook behind in
        # the temp directory.
        handle, temp_path = tempfile.mkstemp(prefix="plexus-playbook-", suffix=".yaml")
        os.close(handle)
        self._temp = Path(temp_path)
        # The *sanitised* document, not the operator's text. Atrium's `reload()`
        # re-reads this file at the start of every transaction and takes
        # `log_path` from it again, so handing over the original would leave the
        # reload path an escape even with the first load checked.
        self._temp.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")

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

    #: The two moments an injection can happen. Checked, because the two
    #: hooks branch on this string and anything else matched neither — the
    #: node bound, passed traffic through and injected nothing, which is
    #: indistinguishable from a trigger that never fired.
    WHENS = ("before_command", "after_response")

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
        if when not in self.WHENS:
            raise TransformError(
                "When is one of %s; this one says %r."
                % (", ".join(self.WHENS), when)
            )
        self.when = when
        self.repeat = repeat
        self._execute = card_execute
        self.fired = 0
        self.skipped = 0
        #: What the card last said to an injected command. One, not all of
        #: them: `results` held every response for the life of the run and
        #: nothing ever read it, which on a long relay is an unbounded field
        #: full of bytes nobody asked for.
        self.last_response: Optional[bytes] = None
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
            # No out-of-band line to the card. Counted so the miss is visible
            # rather than looking like the trigger never matched — which is
            # what the comment here claimed while the code returned first.
            self.skipped += 1
            log.warning("injection has no out-of-band line to the card; skipped")
            return
        self.fired += 1
        self.last_response = self._execute(self.payload)


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
        # Set by the engine at bind time. Without it a breakpoint was silent:
        # it caught a command, blocked the chain, and told nobody — so the one
        # thing you had to know in order to release it was the one thing you
        # could not find out.
        self.notify: Optional[Callable[[bytes], None]] = None

    def on_command(self, apdu: bytes) -> bytes:
        if not self.enabled or (
            self.match is not None and (len(apdu) < 2 or apdu[1] != self.match)
        ):
            return apdu

        self.hits += 1
        self.held = apdu
        self.replacement = None
        self._released.clear()
        if self.notify is not None:
            try:
                self.notify(apdu)
            except Exception:  # noqa: BLE001 - announcing must not hold the card
                pass
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

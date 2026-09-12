"""
What a node has to be, and how a chain is composed from them.

Three shapes, matching the three port arrangements the catalogue allows:

    CardNode      only answers   — connect / atr / transmit / close
    TransformNode answers and asks — on_command / on_response
    TerminalNode  only asks      — drive(channel, stop)

The first two are atrium's existing contracts under different names, which is
why this file is short. The third is new, and it is the one that inverts
control: a terminal is not called, it *drives*. A real terminal decides when to
send and what to send next, and the engine's job is to hand it something that
looks like a card and then get out of the way.

A chain composes transforms around a card into one object with a `transmit`,
and that object is what a terminal drives. Commands run through the transforms
outward-in, responses back through them inward-out — which is the only ordering
that makes a transform's two hooks bracket the same exchange.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Callable, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class CardNode(Protocol):
    """Something that answers APDUs. Atrium's ``CardTransport``, renamed."""

    def connect(self) -> None: ...

    def get_atr(self) -> bytes: ...

    def transmit(self, apdu: bytes) -> bytes: ...

    def disconnect(self) -> None: ...


@runtime_checkable
class TransformNode(Protocol):
    """
    Something that sits in the conversation. Atrium's mutation hooks.

    Both hooks must return bytes, and returning the argument unchanged is a
    perfectly good implementation — a trace tap does exactly that.
    """

    def on_command(self, apdu: bytes) -> bytes: ...

    def on_response(self, command: bytes, response: bytes) -> bytes: ...


class TerminalNode(Protocol):
    """Something that asks. It owns the loop; the engine does not."""

    def drive(self, channel: "Chain", stop: Callable[[], bool]) -> None: ...


class ChainError(RuntimeError):
    """The chain could not carry an exchange. User-facing."""


class Chain:
    """
    The card, wrapped in its transforms, as one thing a terminal can drive.

    Every exchange is timed and reported before it is returned, so telemetry
    costs the caller nothing and cannot be forgotten at a call site.
    """

    def __init__(
        self,
        card: CardNode,
        transforms: List[TransformNode],
        *,
        card_id: str = "card",
        transform_ids: Optional[List[str]] = None,
        observer: Optional["ExchangeObserver"] = None,
    ) -> None:
        self._card = card
        self._transforms = transforms
        self._card_id = card_id
        self._transform_ids = transform_ids or [f"t{i}" for i in range(len(transforms))]
        self._observer = observer
        self.exchanges = 0
        self.errors = 0

        # One card, one conversation at a time. The terminal drives on its own
        # thread and the console can push an APDU from another; two exchanges
        # interleaving on one card produces a transcript that never happened.
        self._lock = threading.Lock()

        # Set means running. Held clear by `pause`, which stops traffic without
        # unbinding anything — the card stays powered and the readers stay
        # claimed, so `resume` picks up mid-session rather than starting over.
        self._resumed = threading.Event()
        self._resumed.set()

    def pause(self) -> None:
        self._resumed.clear()

    def resume(self) -> None:
        self._resumed.set()

    @property
    def paused(self) -> bool:
        return not self._resumed.is_set()

    @contextlib.contextmanager
    def exclusive(self, timeout: float = 0.0):
        """
        Hold the chain so nothing can begin an exchange on it.

        Yields True when it was actually obtained. `Engine.stop` uses this to
        disconnect the card only while no command is in flight: a join that
        timed out leaves the driver mid-exchange, and pulling the card out from
        under a command leaves it in a state nobody can describe — which is the
        thing `stop` exists to avoid. Releasing anyway beats never releasing,
        so a refusal is reported rather than raised.
        """
        got = self._lock.acquire(timeout=timeout)
        try:
            yield got
        finally:
            if got:
                self._lock.release()

    def get_atr(self) -> bytes:
        with self._lock:
            return self._card.get_atr()

    def transmit(self, apdu: bytes, *, wait: Optional[float] = None) -> bytes:
        """
        One command out, one response back, through every transform.

        `wait` bounds how long this sits waiting its turn — for a pause to be
        lifted, or for the exchange already in flight to finish. The driver
        passes nothing, because a pause is meant to hold it indefinitely; the
        console passes a few seconds, because a console thread parked forever
        cannot even be told to stop. A breakpoint holding the chain used to
        park it exactly that way.
        """
        # Waited on outside the lock, or a pause would hold the lock and a
        # console `resume` could never get in.
        if not self._resumed.wait(timeout=wait):
            raise ChainError("The chain is paused. Resume it, or stop.")
        if not self._lock.acquire(timeout=-1 if wait is None else wait):
            raise ChainError(
                "The chain has been busy for more than %.0f seconds — a "
                "breakpoint is holding it, or the card has not answered."
                % wait
            )
        try:
            return self._transmit(apdu)
        finally:
            self._lock.release()

    def _transmit(self, apdu: bytes) -> bytes:
        started = time.perf_counter()
        command = apdu

        # Announced before anything happens, so the trace reads in the order
        # events actually occurred: the command, then whatever rewrote it, then
        # what came back. Reporting the pair at the end put every rewrite above
        # the command that caused it.
        if self._observer is not None:
            self._observer.started(apdu)

        for node_id, transform in zip(self._transform_ids, self._transforms):
            rewritten = as_bytes(transform.on_command(command))
            if rewritten != command and self._observer is not None:
                self._observer.rewrote(node_id, "command", command, rewritten)
            command = rewritten

        try:
            response = as_bytes(self._card.transmit(command))
        except Exception as exc:
            self.errors += 1
            if self._observer is not None:
                self._observer.failed(self._card_id, exc)
            raise ChainError(f"{self._card_id}: {exc}") from exc

        # What the card itself said, before anything downstream touched it.
        # The trace reports this and then each rewrite, so the difference
        # between what the card answered and what the terminal was told is
        # visible rather than inferred.
        from_card = response
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if self._observer is not None:
            self._observer.answered(from_card, elapsed_ms)

        # A transform that wants to know how long the card took is told here,
        # which is the only place that knows. The timing node's whole purpose is
        # to react to a slow card, and until this call existed it was inert: it
        # bound, passed traffic through and counted nothing, while its own
        # docstring said the engine called it.
        for node_id, transform in zip(self._transform_ids, self._transforms):
            noticed = getattr(transform, "note_elapsed", None)
            if noticed is None:
                continue
            try:
                if noticed(elapsed_ms) and self._observer is not None:
                    self._observer.slow(node_id, elapsed_ms)
            except Exception as exc:  # noqa: BLE001 - never fail an exchange for this
                if self._observer is not None:
                    self._observer.failed(node_id, exc)

        # Outward-in on the way down, inward-out on the way back, so a
        # transform's two hooks bracket the same exchange.
        for node_id, transform in zip(
            reversed(self._transform_ids), reversed(self._transforms)
        ):
            rewritten = as_bytes(transform.on_response(command, response))
            if rewritten != response and self._observer is not None:
                self._observer.rewrote(node_id, "response", response, rewritten)
            response = rewritten

        self.exchanges += 1
        if self._observer is not None:
            self._observer.exchanged(apdu, command, response, elapsed_ms)
        return response


class ExchangeObserver(Protocol):
    """
    How the engine hears about what crossed the chain.

    Called in the order things happen — started, rewrote…, answered, rewrote…,
    exchanged — so a listener that simply prints produces a readable trace with
    no sorting of its own.
    """

    def started(self, asked: bytes) -> None: ...

    def answered(self, from_card: bytes, elapsed_ms: float) -> None: ...

    def slow(self, node_id: str, elapsed_ms: float) -> None: ...

    def exchanged(
        self, asked: bytes, sent: bytes, response: bytes, elapsed_ms: float
    ) -> None: ...

    def rewrote(self, node_id: str, phase: str, before: bytes, after: bytes) -> None: ...

    def failed(self, node_id: str, error: BaseException) -> None: ...


def as_bytes(value) -> bytes:
    """
    Atrium's hooks return whatever type they were given, and its relay path
    historically dealt in ``str``.  Plexus deals in bytes everywhere, so the
    one place that could see either normalises it.
    """
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("latin-1")
    if isinstance(value, (list, tuple)):
        return bytes(value)
    raise ChainError(f"A node returned {type(value).__name__}, which is not an APDU.")

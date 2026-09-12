"""
The engine: a graph in, a running relay out.

Three phases, and each one refuses rather than improvises.

**Check.** The graph is validated here, not trusted. The editor runs the same
rules, but a graph arriving over a socket is untrusted input that names hardware
and dials addresses, so its verdict is re-derived rather than taken.

**Bind.** Nodes are built in chain order and claimed in one pass. If anything
refuses — a missing capture, a reader that is not there — everything already
bound is released before the error surfaces. A half-bound rig that holds a
reader open is worse than one that never started.

**Run.** The terminal drives on a worker thread; `stop()` sets a flag the
driver checks between exchanges rather than killing anything mid-APDU. A card
interrupted between the command and the response is a card left in a state
nobody can describe.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from plexus.catalogue import CATALOGUE
from plexus.graph import Graph, Problem, chain as chain_of, validate
from plexus.nodes import card as card_nodes
from plexus.nodes import terminal as terminal_nodes
from plexus.nodes import transform as transform_nodes
from plexus.runtime.events import (
    ApduEvent,
    Clock,
    EdgeTelemetry,
    LogLine,
    NodeTelemetry,
    RollingMean,
    Sink,
)
from plexus.runtime.ports import Chain

log = logging.getLogger(__name__)


class EngineError(RuntimeError):
    """The graph cannot be armed. User-facing; the message says what to fix."""


class Engine:
    """One graph, one run. Build a new one to run a different graph."""

    def __init__(self, sink: Optional[Sink] = None) -> None:
        self.sink = sink or Sink()
        self.clock = Clock()
        self.graph: Optional[Graph] = None
        self.problems: List[Problem] = []

        self._chain: Optional[Chain] = None
        self._terminal = None
        self._card = None
        self._transforms: List[Any] = []
        self._transform_ids: List[str] = []
        self._node_ids: List[str] = []
        self._edge_ids: List[str] = []

        self._telemetry: Dict[str, NodeTelemetry] = {}
        self._means: Dict[str, RollingMean] = {}
        self._edges: Dict[str, EdgeTelemetry] = {}

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._seq = 0
        self._state = "stopped"

    # ── State ───────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _set_state(self, state: str, detail: Optional[str] = None) -> None:
        self._state = state
        self.sink.state(state, detail)

    def _log(self, level: str, text: str, source: str = "plexus") -> None:
        self.sink.log(LogLine(self.clock.ms(), level, source, text))

    # ── Arm ─────────────────────────────────────────────────────────────

    def arm(self, graph: Graph, devices=()) -> None:
        """Check the graph, bind every node, and start driving."""
        if self.running:
            raise EngineError("Already running. Stop first.")

        self.graph = graph
        self.clock.reset()
        self._seq = 0
        self._set_state("arming")

        self.problems = validate(graph, devices)
        blocking = [p for p in self.problems if p.blocking]
        for problem in self.problems:
            self._log("error" if problem.blocking else "warn", problem.message, "check")
        if blocking:
            self._set_state("faulted", blocking[0].message)
            raise EngineError(blocking[0].message)

        steps = chain_of(graph)
        if len(steps) < 2:
            message = "No complete chain: a graph needs something that asks and something that answers."
            self._set_state("faulted", message)
            raise EngineError(message)

        try:
            self._bind(steps)
        except Exception as exc:
            self._release()
            self._set_state("faulted", str(exc))
            raise EngineError(str(exc)) from exc

        self._log(
            "info",
            "Armed %d nodes: %s" % (len(steps), " -> ".join(n.id for n in steps)),
        )
        self._set_state("running")

        self._stop.clear()
        self._thread = threading.Thread(target=self._drive, name="plexus-relay", daemon=True)
        self._thread.start()

    def _bind(self, steps) -> None:
        """Build every node in chain order. Raises on the first refusal."""
        head, tail = steps[0], steps[-1]
        middle = steps[1:-1]

        self._node_ids = [n.id for n in steps]
        self._edge_ids = []
        for node in steps[:-1]:
            edge = next(
                (
                    e
                    for e in self.graph.edges
                    if e.source.node == node.id and e.source.port != "oob"
                ),
                None,
            )
            self._edge_ids.append(edge.id if edge else f"{node.id}:card")

        for node_id in self._node_ids:
            self._telemetry[node_id] = NodeTelemetry(status="idle")
            self._means[node_id] = RollingMean()
        for edge_id in self._edge_ids:
            self._edges[edge_id] = EdgeTelemetry()

        # Card first: transforms may need an out-of-band line to it, and a
        # transform built against a card that then fails to open would have to
        # be torn down again.
        self._card = card_nodes.build(tail.type, tail.config)
        self._card.connect()
        self._mark(tail.id, status="ready")

        def card_execute(apdu: bytes) -> bytes:
            """The dashed edge: straight to the card, past everything else."""
            self._mark_oob(tail.id)
            return self._card.transmit(apdu)

        self._transforms = []
        self._transform_ids = []
        for node in middle:
            built = transform_nodes.build(node.type, node.config, card_execute=card_execute)
            self._transforms.append(built)
            self._transform_ids.append(node.id)
            self._mark(node.id, status="ready")

        self._terminal = terminal_nodes.build(head.type, head.config)
        self._mark(head.id, status="ready")

        self._chain = Chain(
            self._card,
            self._transforms,
            card_id=tail.id,
            transform_ids=self._transform_ids,
            observer=_Observer(self),
        )

    # ── Run ─────────────────────────────────────────────────────────────

    def _drive(self) -> None:
        # Held locally: releasing clears `self._chain`, and reading it back
        # afterwards is how the first cut of this ended — an AttributeError
        # raised on the relay thread, where nothing was watching for it.
        chain, terminal = self._chain, self._terminal
        if chain is None or terminal is None:
            return

        try:
            terminal.drive(chain, self._stop.is_set)
        except Exception as exc:
            self._log("error", str(exc), "runtime")
            self._mark(self._node_ids[0], status="error", detail=str(exc))
            self._release_if_idle()
            self._set_state("faulted", str(exc))
            return

        exchanges = chain.exchanges
        if not self._stop.is_set():
            self._settle()
            self._log(
                "info",
                "The terminal finished after %d exchanges. The card is still bound — "
                "send APDUs with tx, or stop to release it." % exchanges,
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the driver to stop between exchanges, then release everything."""
        if self._thread is None:
            self._release()
            self._set_state("stopped")
            return

        self._set_state("stopping")
        self._stop.set()
        # A pause or a breakpoint holds the driver mid-exchange; let both go so
        # the thread can reach its next stop check instead of hanging on join.
        if self._chain is not None:
            self._chain.resume()
        for transform in self._transforms:
            release = getattr(transform, "release", None)
            if release is not None:
                release()

        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            self._log("warn", "The driver did not stop in time; releasing anyway.")
        self._thread = None

        self._release()
        self._log("info", "Stopped. Devices released.")
        self._set_state("stopped")

    def pause(self) -> None:
        """Hold traffic without unbinding. The card stays powered."""
        if self._chain is not None:
            self._chain.pause()

    def resume(self) -> None:
        if self._chain is not None:
            self._chain.resume()

    @property
    def paused(self) -> bool:
        return self._chain is not None and self._chain.paused

    def transmit(self, apdu: bytes) -> bytes:
        """
        Push one command through the chain by hand.

        This is the console's `tx`. It takes the same lock the terminal's
        driver does, so a hand-sent APDU waits its turn rather than
        interleaving with the exchange already in flight.
        """
        if self._chain is None:
            raise EngineError("Nothing is armed.")
        return self._chain.transmit(apdu)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the driver finishes. True when it did."""
        if self._thread is None:
            return True
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    # ── Release ─────────────────────────────────────────────────────────

    def _release_if_idle(self) -> None:
        """Nothing to do: a fault leaves the rig bound so it can be inspected."""
        if self._stop.is_set():
            return  # stop() is already doing it
        self._settle()

    def _settle(self) -> None:
        """
        The driver has finished, but nothing has been let go.

        A scripted terminal — a probe, a replay — runs out of script; that is
        not a reason to drop the card. The console's `tx` is the most useful
        thing in the tool *after* a probe has told you what the card is, and
        releasing here would take it away. So the nodes stop claiming to be
        live and everything stays bound until someone stops it.

        A relay terminal never reaches here: it loops until told otherwise.
        """
        for node_id in self._node_ids:
            if self._telemetry.get(node_id, NodeTelemetry()).status != "error":
                self._mark(node_id, status="ready")

    def _release(self) -> None:
        """Close everything that was opened, loudest-first, never raising."""
        for transform in self._transforms:
            closer = getattr(transform, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:  # pragma: no cover - shutdown is best effort
                    log.debug("transform close failed", exc_info=True)
        self._transforms = []
        self._transform_ids = []

        if self._card is not None:
            try:
                self._card.disconnect()
            except Exception:  # pragma: no cover
                log.debug("card disconnect failed", exc_info=True)
        self._card = None
        self._terminal = None
        self._chain = None

        for node_id in self._node_ids:
            self._mark(node_id, status="idle")

    # ── Telemetry ───────────────────────────────────────────────────────

    def _mark(self, node_id: str, **fields) -> None:
        telemetry = self._telemetry.setdefault(node_id, NodeTelemetry())
        for key, value in fields.items():
            setattr(telemetry, key, value)
        self.sink.node(node_id, telemetry)

    def _mark_oob(self, node_id: str) -> None:
        telemetry = self._telemetry.setdefault(node_id, NodeTelemetry())
        telemetry.apdus += 1
        self.sink.node(node_id, telemetry)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq - 1

    def devices(self) -> List[Any]:
        """
        Hardware the runtime can see.

        Empty in this build, and deliberately so rather than invented: device
        discovery belongs with the adapters that would bind the devices, and
        claiming a reader exists that nothing can open would be worse than
        saying nothing. The graph checks treat an unknown device id as unknown
        rather than missing, so an empty list does not block a graph — it just
        means nothing can be confirmed present.
        """
        return []

    def telemetry(self, node_id: str) -> NodeTelemetry:
        return self._telemetry.get(node_id, NodeTelemetry())

    def edge_telemetry(self, edge_id: str) -> EdgeTelemetry:
        return self._edges.get(edge_id, EdgeTelemetry())


class _Observer:
    """Turns what the chain saw into frames, without the chain knowing how."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._t0 = 0.0

    def started(self, asked: bytes) -> None:
        engine = self._engine
        self._t0 = engine.clock.ms()
        edge = engine._edge_ids[0] if engine._edge_ids else "chain"
        engine.sink.apdu(
            ApduEvent(engine._next_seq(), self._t0, edge, "command", asked.hex().upper())
        )

    def answered(self, from_card: bytes, elapsed_ms: float) -> None:
        """What the card said, before anything downstream rewrote it."""
        engine = self._engine
        edge = engine._edge_ids[-1] if engine._edge_ids else "chain"
        engine.sink.apdu(
            ApduEvent(
                engine._next_seq(),
                self._t0 + elapsed_ms,
                edge,
                "response",
                from_card.hex().upper(),
                ms=elapsed_ms,
            )
        )

    def exchanged(self, asked: bytes, sent: bytes, response: bytes, elapsed_ms: float) -> None:
        engine = self._engine

        for node_id in engine._node_ids:
            telemetry = engine._telemetry.setdefault(node_id, NodeTelemetry())
            telemetry.status = "live"
            telemetry.apdus += 1
            telemetry.last_ms = elapsed_ms
            telemetry.mean_ms = engine._means.setdefault(node_id, RollingMean()).add(elapsed_ms)
            engine.sink.node(node_id, telemetry)

        for edge_id in engine._edge_ids:
            edge = engine._edges.setdefault(edge_id, EdgeTelemetry())
            edge.apdus += 1
            edge.last_ms = elapsed_ms
            edge.bytes_out += len(sent)
            edge.bytes_in += len(response)
            engine.sink.edge(edge_id, edge)

    def rewrote(self, node_id: str, phase: str, before: bytes, after: bytes) -> None:
        engine = self._engine
        # Attributed to the edge the rewritten bytes leave on: a command goes
        # on downstream, a response goes back up.
        edges = engine._edge_ids or ["chain"]
        edge_id = edges[-1] if phase == "command" else edges[0]
        engine.sink.apdu(
            ApduEvent(
                engine._next_seq(),
                engine.clock.ms(),
                edge_id,
                phase,
                after.hex().upper(),
                mutated_by=node_id,
                note="%d bytes in, %d out" % (len(before), len(after)),
            )
        )

    def failed(self, node_id: str, error: BaseException) -> None:
        engine = self._engine
        telemetry = engine._telemetry.setdefault(node_id, NodeTelemetry())
        telemetry.errors += 1
        telemetry.status = "error"
        telemetry.detail = str(error)
        engine.sink.node(node_id, telemetry)
        engine._log("error", "%s: %s" % (node_id, error), "runtime")


def node_implementations() -> Dict[str, str]:
    """
    Which catalogue keys this build can actually bind, for `plexus nodes`.

    Derived from the node modules rather than a list kept beside them, so it
    cannot drift from what would really happen at bind time.
    """
    out: Dict[str, str] = {}
    for node_type in CATALOGUE:
        key = node_type.key
        if node_type.node_class == "card":
            out[key] = "pending" if key in card_nodes.PENDING else "ready"
        elif node_type.node_class == "terminal":
            out[key] = "pending" if key in terminal_nodes.PENDING else "ready"
        else:
            out[key] = "ready"
    return out


__all__ = ["Engine", "EngineError", "node_implementations"]


# Silence the unused-import warning for a name kept for callers' convenience.
_ = Callable

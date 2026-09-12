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
from plexus.graph import Graph, Problem, validate
from plexus.graph import chain as chain_of
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
from plexus.runtime.ports import Chain, as_bytes

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
        self._transform_by_id: Dict[str, Any] = {}
        self._node_ids: List[str] = []
        self._edge_ids: List[str] = []

        self._telemetry: Dict[str, NodeTelemetry] = {}
        self._means: Dict[str, RollingMean] = {}
        self._edges: Dict[str, EdgeTelemetry] = {}

        self._thread: Optional[threading.Thread] = None
        # Arm and stop both bind or unbind hardware, and the server can now
        # start a stop while an arm is still in an executor thread. Without
        # this, `_bind` could set `_card` after `_release` had cleared it:
        # a card left open with the runtime reporting "stopped".
        self._gate = threading.RLock()
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
        with self._gate:
            self._arm(graph, devices)

    def _arm(self, graph: Graph, devices=()) -> None:
        if self.running:
            raise EngineError("Already running. Stop first.")

        # ── Checked before anything is touched ──────────────────────────
        #
        # A refused graph has to cost you nothing. This used to clear the
        # previous run's telemetry and release its card *before* discovering
        # that the new graph would not run, so one typo took away a card
        # session that was still perfectly usable — and `tx` with it.
        self.problems = validate(graph, devices)
        blocking = [p for p in self.problems if p.blocking]
        steps = chain_of(graph)
        if not blocking and len(steps) < 2:
            blocking = [
                Problem(
                    "no-chain",
                    "error",
                    "No complete chain: a graph needs something that asks and "
                    "something that answers.",
                )
            ]
            self.problems = self.problems + blocking

        for problem in self.problems:
            self._log("error" if problem.blocking else "warn", problem.message, "check")

        if blocking:
            if self._card is None and not self._transforms:
                # Nothing was running, so "faulted" is the honest answer to a
                # Run that did not.
                self._set_state("faulted", blocking[0].message)
            else:
                # Something from the last run is still bound and still usable.
                # Saying "faulted" here would take away the Stop button that
                # belongs to it.
                self._log(
                    "warn",
                    "Nothing was released — the previous rig is still bound.",
                    "runtime",
                )
            raise EngineError(blocking[0].message)

        # ── Past the point of no return ─────────────────────────────────
        #
        # `running` only asks whether the relay thread is alive, and two paths
        # deliberately leave everything bound with a dead thread: a fault
        # (`_release_if_idle`) so the rig can be inspected, and a script that
        # ran out (`_settle`) so `tx` still reaches the card. In both, the
        # editor shows Run again — `isRunning` is false for `faulted`, and
        # `running` is the state a finished probe leaves — so the next press
        # arrived here with a card open, and `_bind` overwrote `self._card` and
        # `self._transforms` without closing either. One reader claimed twice,
        # and nothing left holding the first claim.
        if self._card is not None or self._transforms or self._chain is not None:
            self._log("info", "Releasing what the last run left bound.", "runtime")
            self._release()

        self.graph = graph
        self.clock.reset()
        self._seq = 0

        # Cleared here, not in _bind, which only ever writes the nodes of the graph it
        # is binding. Arming a *different* graph in the same session otherwise left the
        # previous one's counters in place, and `Session._hello` replays all of
        # `_telemetry` to a reconnecting editor — so it would be told about nodes that
        # are not in the graph it is looking at.
        self._telemetry.clear()
        self._means.clear()
        self._edges.clear()
        self._node_ids = []
        self._edge_ids = []

        self._set_state("arming")

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
        self._thread = threading.Thread(
            target=self._drive, name="plexus-relay", daemon=True
        )
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

        # Terminal first, because it is the one that costs nothing to refuse.
        # It is built rather than connected — nothing is claimed here — so a
        # node this build cannot drive says so before a reader is opened.
        self._terminal = terminal_nodes.build(head.type, head.config)
        self._mark(head.id, status="ready")

        # Card next: transforms may need an out-of-band line to it, and a
        # transform built against a card that then fails to open would have to
        # be torn down again.
        self._card = card_nodes.build(tail.type, tail.config)
        self._card.connect()
        self._mark(tail.id, status="ready")

        # The dashed line. Only `inject` has an out-of-band port, and
        # `card_execute` used to be handed to every transform regardless of
        # whether one was drawn — so an inject node with no dashed edge
        # injected anyway, and one whose dashed edge pointed elsewhere injected
        # into the card all the same. The edge is what authorises it now.
        oob_by_node: Dict[str, str] = {}
        for node in middle:
            edge = next(
                (
                    e
                    for e in self.graph.edges
                    if e.source.node == node.id and e.source.port == "oob"
                ),
                None,
            )
            if edge is None:
                continue
            if edge.target.node != tail.id:
                raise EngineError(
                    "%s's out-of-band line goes to %s, but the card in this "
                    "chain is %s. Draw it to the card, or remove it."
                    % (node.id, edge.target.node, tail.id)
                )
            oob_by_node[node.id] = edge.id

        self._transforms = []
        self._transform_ids = []
        self._transform_by_id = {}
        for node in middle:
            oob_edge = oob_by_node.get(node.id)
            built = transform_nodes.build(
                node.type,
                node.config,
                card_execute=(
                    self._oob_line(node.id, oob_edge) if oob_edge else None
                ),
            )
            # A breakpoint that catches a command blocks the chain, so it has to
            # be able to say so. Set by attribute rather than by constructor
            # argument: only one transform has anything to announce, and
            # `build` should not grow a parameter for it.
            if getattr(built, "notify", "missing") is None:
                built.notify = self._holding(node.id)
            self._transforms.append(built)
            self._transform_ids.append(node.id)
            self._transform_by_id[node.id] = built
            self._mark(node.id, status="ready")

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
            # What the terminal found, if it has anything to say. A probe fills
            # in a whole picture of the card and used to hand it to nobody:
            # `findings`, `aids` and `sent` were read by nothing at all, so the
            # node whose purpose is to tell you what a card *is* told you only
            # how many APDUs went past.
            source = self._node_ids[0] if self._node_ids else "runtime"
            for line in self._terminal_summary():
                self._log("info", line, source)
            self._log(
                "info",
                "The terminal finished after %d exchanges. The card is still bound — "
                "send APDUs with tx, or stop to release it." % exchanges,
            )

    def _terminal_summary(self) -> List[str]:
        summarise = getattr(self._terminal, "summary", None)
        if summarise is None:
            return []
        try:
            return list(summarise())
        except Exception:  # pragma: no cover - a summary is not the run
            log.debug("terminal summary failed", exc_info=True)
            return []

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the driver to stop between exchanges, then release everything."""
        # The flag is set before the gate is taken: an arm may still be binding
        # on another thread, and a driver that starts in the meantime should
        # already know it is not wanted. `_arm` clears it just before starting,
        # and `_stop_locked` sets it again once it has the gate.
        self._stop.set()
        with self._gate:
            self._stop_locked(timeout)

    def _stop_locked(self, timeout: float) -> None:
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
        # `close`, not `release`. Releasing lets the held command through and
        # leaves the breakpoint armed. Usually that is harmless — the stop flag
        # is already set, so the driver checks it and leaves before sending
        # anything else. But if stop lands just *after* that check, the driver's
        # next command is caught by a breakpoint nobody is going to release: the
        # join then times out and the card is disconnected under a live
        # exchange. `close` sets the event and disarms, so there is no next
        # catch to lose the race to.
        for transform in self._transforms:
            closer = getattr(transform, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:  # pragma: no cover - shutdown is best effort
                    log.debug("transform close failed", exc_info=True)

        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            self._log("warn", "The driver did not stop in time; releasing anyway.")
        self._thread = None

        # Released while nothing is in flight, where that is possible. If the
        # join succeeded the chain is already quiet and this costs nothing; if
        # it did not, one more second is worth spending before disconnecting a
        # card mid-command. Not the full `timeout` again: a driver that ignored
        # the stop flag for five seconds will not free the chain in the next
        # five, and stop has to return.
        chain = self._chain
        if chain is None:
            self._release()
        else:
            with chain.exclusive(timeout=min(timeout, 1.0)) as quiet:
                if not quiet:
                    self._log(
                        "warn",
                        "An exchange was still in flight. Releasing anyway — "
                        "the card may be left mid-command.",
                    )
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

    # ── Breakpoints ─────────────────────────────────────────────────────
    #
    # A breakpoint is the one transform that stops rather than rewrites, so it
    # is the one that needs a way out. The catalogue has always said "until you
    # release it from the console"; for a while there was no verb that could,
    # and the only way out of a hit was `stop`.

    #: How long a console `tx` waits for its turn before saying why it cannot
    #: have it. The driver holds the chain for one exchange; longer than this
    #: and something is stuck.
    CONSOLE_WAIT_S = 5.0

    def _holding(self, node_id: str) -> Callable[[bytes], None]:
        """The callback a breakpoint calls the moment it catches something."""

        def caught(apdu: bytes) -> None:
            self._mark(node_id, detail="holding %s" % apdu.hex().upper())
            self._log(
                "warn",
                "%s is holding %s. Release it with `release`, or stop."
                % (node_id, apdu.hex().upper()),
                "runtime",
            )

        return caught

    def held(self) -> List[str]:
        """Breakpoints with a command in hand, in chain order."""
        return [
            node_id
            for node_id in self._transform_ids
            if getattr(self._transform_by_id.get(node_id), "held", None) is not None
        ]

    def release(
        self, node_id: Optional[str] = None, replacement: Optional[bytes] = None
    ) -> str:
        """
        Let a held breakpoint go, optionally with different bytes.

        Returns the node that was holding, so the console can say which.
        """
        holding = self.held()
        if not holding:
            raise EngineError("Nothing is held.")
        if node_id is not None:
            if node_id not in holding:
                raise EngineError("%s is not holding anything." % node_id)
            holding = [node_id]

        chosen = holding[0]
        transform = self._transform_by_id[chosen]
        if replacement is not None and not getattr(transform, "editable", False):
            raise EngineError(
                "%s does not allow editing the held bytes." % chosen
            )
        transform.release(replacement)
        self._mark(chosen, detail=None)
        return chosen

    def transmit(self, apdu: bytes) -> bytes:
        """
        Push one command through the chain by hand.

        This is the console's `tx`. It takes the same lock the terminal's
        driver does, so a hand-sent APDU waits its turn rather than
        interleaving with the exchange already in flight — but it waits with a
        bound, and refuses outright when it can already name what is in the
        way. A console thread parked on a breakpoint cannot be told to stop.
        """
        if self._chain is None:
            raise EngineError("Nothing is armed.")
        holding = self.held()
        if holding:
            raise EngineError(
                "%s is holding a command. Release it first, or stop." % holding[0]
            )
        return self._chain.transmit(apdu, wait=self.CONSOLE_WAIT_S)

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
        self._transform_by_id = {}

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

    def _oob_line(self, node_id: str, edge_id: str) -> Callable[[bytes], bytes]:
        """
        The dashed edge: straight to the card, past every other transform.

        Reported like any other exchange, because it was not reported at all.
        No APDU event was emitted and the dashed edge's own telemetry stayed at
        zero for the life of the run, so an injection that reached the card
        looked exactly like one that never happened — on a node whose entire
        purpose is to send a command the terminal did not ask for.
        """

        def execute(apdu: bytes) -> bytes:
            card = self._card
            if card is None:
                raise EngineError("Nothing is bound.")

            started = self.clock.ms()
            self.sink.apdu(
                ApduEvent(
                    self._next_seq(),
                    started,
                    edge_id,
                    "command",
                    apdu.hex().upper(),
                    mutated_by=node_id,
                    note="out of band",
                )
            )
            response = as_bytes(card.transmit(apdu))
            elapsed = self.clock.ms() - started
            self.sink.apdu(
                ApduEvent(
                    self._next_seq(),
                    started + elapsed,
                    edge_id,
                    "response",
                    response.hex().upper(),
                    ms=elapsed,
                    mutated_by=node_id,
                    note="out of band",
                )
            )

            # Both ends of the dashed edge: the node that sent it and the card
            # that answered.
            self._mark_oob(node_id)
            if self._node_ids:
                self._mark_oob(self._node_ids[-1])

            edge = self._edges.setdefault(edge_id, EdgeTelemetry())
            edge.apdus += 1
            edge.last_ms = elapsed
            edge.bytes_out += len(apdu)
            edge.bytes_in += len(response)
            self.sink.edge(edge_id, edge)

            # The count, on the node card. `fired` was incremented and read by
            # nobody, so the one thing an inject node does left no mark on the
            # interface at all.
            fired = getattr(self._transform_by_id.get(node_id), "fired", None)
            if isinstance(fired, int):
                self._mark(node_id, detail="injected %d" % fired)
            return response

        return execute

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

    def exchanged(
        self, asked: bytes, sent: bytes, response: bytes, elapsed_ms: float
    ) -> None:
        engine = self._engine

        for node_id in engine._node_ids:
            telemetry = engine._telemetry.setdefault(node_id, NodeTelemetry())
            telemetry.status = "live"
            telemetry.apdus += 1
            telemetry.last_ms = elapsed_ms
            telemetry.mean_ms = engine._means.setdefault(
                node_id, RollingMean()
            ).add(elapsed_ms)
            engine.sink.node(node_id, telemetry)

        for edge_id in engine._edge_ids:
            edge = engine._edges.setdefault(edge_id, EdgeTelemetry())
            edge.apdus += 1
            edge.last_ms = elapsed_ms
            edge.bytes_out += len(sent)
            edge.bytes_in += len(response)
            engine.sink.edge(edge_id, edge)

    def slow(self, node_id: str, elapsed_ms: float) -> None:
        """
        A timing node decided the card was slow enough to ask for more time.

        Reported rather than silently counted, because the node cannot actually
        send the S(WTX) block itself — only whoever owns the RF or contact
        interface can — so the operator seeing that it fired is the whole value
        of having it in the chain today.
        """
        engine = self._engine
        telemetry = engine._telemetry.setdefault(node_id, NodeTelemetry())
        transform = engine._transform_by_id.get(node_id)
        requested = getattr(transform, "requested", None)
        telemetry.detail = (
            "asked for more time %d time(s)" % requested
            if isinstance(requested, int)
            else "asked for more time"
        )
        engine.sink.node(node_id, telemetry)
        engine._log(
            "warn",
            "%s: the card took %.1f ms; asked the terminal for more time."
            % (node_id, elapsed_ms),
            "runtime",
        )

    def rewrote(self, node_id: str, phase: str, before: bytes, after: bytes) -> None:
        engine = self._engine

        # Attributed to the edge the rewritten bytes actually leave on.
        #
        # `_edge_ids[i]` is the edge leaving `_node_ids[i]`. A command goes on
        # downstream, so it leaves on that one; a response goes back upstream,
        # so it leaves on the edge this node was reached by, `_edge_ids[i - 1]`.
        #
        # This used to take the ends of the list — last for a command, first
        # for a response — which is correct for a chain with one transform,
        # because there the edge out of that node *is* the edge into the card.
        # With two, every rewrite by the first transform was drawn at the card.
        edges = engine._edge_ids
        try:
            index = engine._node_ids.index(node_id)
        except ValueError:
            index = -1

        if not edges or index < 0:
            edge_id = "chain"
        elif phase == "command":
            edge_id = edges[min(index, len(edges) - 1)]
        else:
            edge_id = edges[max(index - 1, 0)]

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


def node_requirements() -> Dict[str, str]:
    """
    What each node this build cannot bind is waiting on.

    The same tables `build` raises from, so the sentence a palette shows
    before you drag a node in and the sentence `arm` gives you afterwards
    are the same sentence.
    """
    out: Dict[str, str] = {}
    out.update(card_nodes.PENDING)
    out.update(terminal_nodes.PENDING)
    return out


__all__ = [
    "Engine",
    "EngineError",
    "node_implementations",
    "node_requirements",
]


# Silence the unused-import warning for a name kept for callers' convenience.
_ = Callable

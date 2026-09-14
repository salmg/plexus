"""
One editor, one engine, one socket.

The hard part here is that the two halves live in different worlds. The engine
drives a card on a worker thread and emits frames from it; the socket is
asyncio. Frames therefore cross a thread-safe queue and are drained by a pump
task, and **the relay is never made to wait for the socket**. A listener that
has stopped reading must not be able to stall something holding a card.

That is also why the queue is bounded and drops rather than blocks. PROTOCOL.md
says a runtime may drop `apdu` frames under load but must not drop `node`,
`edge` or `state`, so that is exactly what happens: the trace thins out, the
picture stays correct, and the console says how many were lost.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any, Awaitable, Callable, Dict, List, Optional

from plexus.api import console
from plexus.graph import Graph, GraphError
from plexus.runtime import Engine, EngineError, node_requirements
from plexus.runtime.events import CallbackSink

log = logging.getLogger(__name__)

#: How long the pump gathers before sending. PROTOCOL.md suggests 20–50 ms; a
#: relay at speed produces far more telemetry than a browser wants as separate
#: messages, and one frame per tick is kinder to both ends.
BATCH_WINDOW_S = 0.025

#: Frames held before the trace starts thinning. Roughly a second of a fast
#: relay, which is more than a person can read and less than a memory problem.
STREAM_LIMIT = 2000

#: Frames from the editor waiting to be worked, in order. Small on purpose: an
#: editor sends one thing at a time, so a queue this deep is a client bug or an
#: attempt to make the runtime hold work it will never get to.
INBOX_LIMIT = 32

PROTOCOL_VERSION = 1

Sender = Callable[[Dict[str, Any]], Awaitable[None]]

class Runtime:
    """
    The engine, owned by the process rather than by a socket.

    PROTOCOL.md promises that losing the socket is not losing the run, and for
    a while that was simply false: every connection built its own Engine and
    `Session.close` stopped it, so a browser reload released the card in the
    middle of a transaction, and a reconnecting editor was talking to a
    different engine that had never been armed. The replay in `_hello` was
    written for exactly this and had nothing to replay.

    So: one engine, and a list of listeners it writes to — which is empty
    whenever nobody is connected. That is the normal state of a relay someone
    left running, and the frames it produces in the meantime are dropped
    rather than queued, because there is nobody to tell and the run must not
    wait for one to arrive.
    """

    def __init__(self) -> None:
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._listeners_lock = threading.Lock()
        self.engine = Engine(sink=CallbackSink(self._fan_out))
        #: The graph that is armed, or the last one offered. On the runtime
        #: rather than the session, so `status` and `check` answer about what
        #: is actually running after the editor that armed it has gone.
        self.graph: Optional[Graph] = None

    def _fan_out(self, frame: Dict[str, Any]) -> None:
        """Called from whichever thread produced the frame, the relay included."""
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(frame)
            except Exception:  # pragma: no cover - a listener is not the run
                log.debug("a frame listener failed", exc_info=True)

    def attach(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        with self._listeners_lock:
            self._listeners.append(listener)

    def detach(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        with self._listeners_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    @property
    def listeners(self) -> int:
        with self._listeners_lock:
            return len(self._listeners)

class Session:
    """
    The lifetime of one editor connection.

    It borrows the runtime rather than owning it. Constructing one without a
    runtime gives it a private one, which is what a test wants and what a
    single-shot caller gets.
    """

    def __init__(self, send: Sender, runtime: Optional["Runtime"] = None) -> None:
        self._send = send
        self._stream: "deque[Dict[str, Any]]" = deque()
        self._latest: Dict[Any, Dict[str, Any]] = {}
        self._states: List[Dict[str, Any]] = []
        self._dropped = 0
        self._lock = threading.Lock()
        self._pump: Optional[asyncio.Task] = None
        self._inbox: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=INBOX_LIMIT)
        self._worker: Optional[asyncio.Task] = None
        self.runtime = runtime if runtime is not None else Runtime()
        self._greeted = False
        self.runtime.attach(self._offer)

    @property
    def engine(self) -> Engine:
        return self.runtime.engine

    @property
    def graph(self) -> Optional[Graph]:
        return self.runtime.graph

    @graph.setter
    def graph(self, value: Optional[Graph]) -> None:
        self.runtime.graph = value

    # ── Frames out ──────────────────────────────────────────────────────

    def _offer(self, frame: Dict[str, Any]) -> None:
        """
        Called from whichever thread produced the frame, including the relay.

        Never blocks, never raises, and never does work proportional to how far
        behind the listener is: the caller is in the middle of an exchange with
        a card.

        Frames are sorted by what they *are*, which is what makes all three of
        those promises keepable at once.
        """
        kind = frame.get("type")

        if kind in ("node", "edge"):
            # State, not events. The relay emits one of these per node and per
            # edge on *every* exchange, and only the newest value of each means
            # anything — so they are held by key rather than queued. This was
            # the flood: PROTOCOL.md says node and edge frames must not be
            # dropped, so the old code refused to drop them and grew without
            # limit instead, while paying a full drain-and-refill of the queue
            # for each one. Coalescing keeps the promise, bounds the memory by
            # the size of the graph, and rebuilds an identical picture.
            with self._lock:
                self._latest[(kind, frame.get("id"))] = frame
            return

        if kind == "state":
            # A handful per run, and losing one leaves the editor permanently
            # wrong rather than merely thinner. Kept, whatever happens.
            with self._lock:
                self._states.append(frame)
            return

        # apdu and log: a genuine stream, and the one thing PROTOCOL.md allows
        # a runtime to thin under load. The oldest goes, in constant time.
        with self._lock:
            if len(self._stream) >= STREAM_LIMIT:
                self._stream.popleft()
                self._dropped += 1
            self._stream.append(frame)

    async def start(self) -> None:
        self._pump = asyncio.ensure_future(self._run_pump())
        self._worker = asyncio.ensure_future(self._work())

    async def close(self) -> None:
        for task in (self._worker, self._pump):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker = None
        self._pump = None

        # Deliberately *not* `engine.stop()`. This used to release the card on
        # every disconnect, which made PROTOCOL.md's promise false in the worst
        # way: a browser reload dropped a live relay mid-transaction. The
        # connection ends; the run does not. `stop` ends the run, and so does
        # the process shutting down.
        self.runtime.detach(self._offer)

    async def _run_pump(self) -> None:
        while True:
            await asyncio.sleep(BATCH_WINDOW_S)
            await self.flush()

    async def flush(self) -> None:
        """
        Send everything gathered since the last tick, as one frame if many.

        Order within a tick: what crossed the wire, then what that did to each
        node and edge, then where the run ended up. A state change is the
        conclusion of the window it closes, so it reads last.
        """
        with self._lock:
            frames: List[Dict[str, Any]] = list(self._stream)
            self._stream.clear()
            frames.extend(self._latest.values())
            self._latest.clear()
            frames.extend(self._states)
            del self._states[:]
            dropped, self._dropped = self._dropped, 0

        if dropped:
            frames.append(
                {
                    "type": "log",
                    "line": {
                        "t": self.engine.clock.ms(),
                        "level": "warn",
                        "source": "plexus",
                        "text": "%d trace frames dropped keeping up." % dropped,
                    },
                }
            )

        if not frames:
            return
        await self._deliver(
            frames[0] if len(frames) == 1 else {"type": "batch", "frames": frames}
        )

    async def _deliver(self, frame: Dict[str, Any]) -> None:
        try:
            await self._send(frame)
        except Exception:  # pragma: no cover - the socket went away
            log.debug("send failed; the editor has gone", exc_info=True)

    # ── Frames in ───────────────────────────────────────────────────────

    async def dispatch(self, message: Dict[str, Any]) -> None:
        """
        One frame from the read loop, which must not be made to wait.

        `arm` and `command` both block on hardware — `arm` opens a reader,
        `tx` waits for a card — and the read loop used to await each handler in
        turn. So a `stop` sent while a command was in flight sat unread behind
        the very thing it was meant to interrupt: measured at four seconds for
        a four-second command, with the editor's button stuck on Stop for all
        of it.

        Those two go on a queue worked by one task, so they keep their order
        relative to each other and the read loop returns at once. Everything
        else is answered here: `stop` because it is the frame that has to
        overtake, `hello` because its refusal has to reach the read loop in
        order to close the socket with the right code.
        """
        if message.get("type") in ("arm", "command"):
            try:
                self._inbox.put_nowait(message)
            except asyncio.QueueFull:
                await self._deliver(
                    {
                        "type": "error",
                        "message": "Too many frames in flight; wait for the "
                        "ones already sent to answer.",
                    }
                )
            return
        await self.handle(message)

    async def _work(self) -> None:
        """The one worker for frames that block. Order in, order out."""
        while True:
            message = await self._inbox.get()
            try:
                await self.handle(message)
            except Exception:  # noqa: BLE001 - one bad frame is not the session
                log.exception("frame %r failed", message.get("type"))
            finally:
                self._inbox.task_done()

    async def handle(self, message: Dict[str, Any]) -> None:
        """One frame from the editor."""
        kind = message.get("type")

        # PROTOCOL.md: "The editor dials ws://<host>:<port>/ws and sends
        # `hello` first." Nothing enforced it, so a client that never agreed a
        # protocol version could arm a card relay — the one frame that claims
        # hardware, from a peer whose idea of these frame shapes was never
        # checked against this one.
        if kind != "hello" and not self._greeted:
            await self._deliver(
                {"type": "error", "message": "Send hello first; see PROTOCOL.md."}
            )
            return

        if kind == "hello":
            await self._hello(message)
        elif kind == "arm":
            await self._arm(message)
        elif kind == "stop":
            await self._stop()
        elif kind == "command":
            await self._command(message)
        else:
            await self._deliver({"type": "error", "message": "Unknown frame %r." % kind})

    async def _hello(self, message: Dict[str, Any]) -> None:
        version = message.get("version")
        if version != PROTOCOL_VERSION:
            await self._deliver(
                {
                    "type": "error",
                    "message": "This runtime speaks protocol %d, the editor asked for %r."
                    % (PROTOCOL_VERSION, version),
                }
            )
            raise ProtocolMismatch(str(version))

        self._greeted = True

        # Losing the socket is not losing the run, so a reconnecting editor is
        # told everything it needs to catch up rather than being asked to
        # restart something that is still going.
        await self._deliver({"type": "devices", "devices": []})
        # What this build cannot bind, and what each one needs. Over the
        # socket rather than over `/api/health`, which carries the same
        # answer from the same function: the editor is served from another
        # port, so a fetch to health is cross-origin and the browser will
        # not let it read the reply. This socket is already open and has
        # already passed the Origin check.
        await self._deliver(
            {"type": "implementations", "needs": node_requirements()}
        )
        await self._deliver({"type": "state", "state": self.engine.state})
        for node_id, telemetry in self.engine._telemetry.items():
            await self._deliver(
                {"type": "node", "id": node_id, "telemetry": telemetry.to_frame()}
            )

    async def _arm(self, message: Dict[str, Any]) -> None:
        raw = message.get("graph")
        if not isinstance(raw, dict):
            await self._deliver({"type": "error", "message": "arm needs a graph."})
            return

        try:
            graph = _graph_from_frame(raw)
        except GraphError as exc:
            await self._deliver({"type": "error", "message": str(exc)})
            return

        self.graph = graph
        loop = asyncio.get_running_loop()
        try:
            # The engine validates the graph itself rather than trusting the
            # editor's verdict: this one arrived over a socket, and it names
            # hardware and dials addresses.
            await loop.run_in_executor(None, self.engine.arm, graph)
        except EngineError as exc:
            await self._deliver({"type": "error", "message": str(exc)})
        finally:
            await self.flush()

    async def _stop(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self.engine.stop)
        await self.flush()

    async def _command(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id")
        line = message.get("line")
        if not isinstance(request_id, str) or not isinstance(line, str):
            await self._deliver(
                {"type": "error", "message": "command needs an id and a line."}
            )
            return

        # `tx` reaches the card, so it runs off the event loop like everything
        # else that can block.
        lines, level = await asyncio.get_running_loop().run_in_executor(
            None, console.run, self.engine, self.graph, line
        )
        frame: Dict[str, Any] = {"type": "result", "id": request_id, "lines": lines}
        if level:
            frame["level"] = level
        await self.flush()
        await self._deliver(frame)

class ProtocolMismatch(RuntimeError):
    """The editor asked for a protocol version this runtime does not speak."""

def _graph_from_frame(raw: Dict[str, Any]) -> Graph:
    """
    A graph arrives as the JSON the editor saves, not as YAML.

    Reusing the YAML reader would mean serialising and re-parsing for no
    reason, so the frame is shaped into what that reader expects and handed
    over — one implementation of the rules, not two.
    """
    import yaml

    return Graph.from_yaml(yaml.safe_dump(_normalise(raw), sort_keys=False))

def _normalise(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Turn the editor's JSON node shape into the file's.

    Every shape check here earns its place: this runs on a frame that arrived
    over a socket, and anything it raises that is not a `GraphError` is not
    caught as one. `position.get("x")` on a list, or `from.get("node")` on the
    string spelling, raised AttributeError — which used to escape the read loop
    as "session failed" and release the hardware in the middle of a run.
    """
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list):
        # The same rule `Graph.from_yaml` applies, applied here too — otherwise
        # a missing `nodes` became `[]` on the way through and arrived as a
        # legal empty graph.
        raise GraphError("`nodes` must be a list.")

    nodes = []
    for index, node in enumerate(raw_nodes):
        if not isinstance(node, dict):
            raise GraphError("A node in this graph is not an object.")
        position = node.get("position")
        if position is None:
            position = {}
        if not isinstance(position, dict):
            raise GraphError("nodes[%d].position must be an object." % index)
        entry = {
            "id": node.get("id"),
            "type": node.get("type"),
            "at": [position.get("x", 0), position.get("y", 0)],
        }
        for key in ("label", "host"):
            if node.get(key):
                entry[key] = node[key]
        # `is True`, as in the editor's own reader. See `_node_from`.
        if node.get("disabled") is True:
            entry["disabled"] = True
        if node.get("config"):
            entry["config"] = node["config"]
        nodes.append(entry)

    raw_edges = raw.get("edges")
    if raw_edges is None:
        raw_edges = []
    if not isinstance(raw_edges, list):
        raise GraphError("`edges` must be a list.")

    edges = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            raise GraphError("An edge in this graph is not an object.")
        ends = {}
        for end in ("from", "to"):
            ref = edge.get(end)
            if isinstance(ref, str):
                # The file spelling, sent by something that is not the editor.
                # Taken as-is; `PortRef.parse` is the one that judges it.
                ends[end] = ref
                continue
            if not isinstance(ref, dict):
                raise GraphError(
                    'edges[%d].%s should be {"node", "port"} or "node:port".'
                    % (index, end)
                )
            node_id, port = ref.get("node"), ref.get("port")
            if not isinstance(node_id, str) or not isinstance(port, str):
                raise GraphError(
                    "edges[%d].%s needs a node and a port, both strings."
                    % (index, end)
                )
            ends[end] = "%s:%s" % (node_id, port)
        edges.append({"id": edge.get("id"), "from": ends["from"], "to": ends["to"]})

    return {
        "version": raw.get("version", 1),
        "name": raw.get("name") or "untitled",
        "hosts": raw.get("hosts") or [],
        "nodes": nodes,
        "edges": edges,
    }

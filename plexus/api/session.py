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
import queue
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional

from plexus.api import console
from plexus.graph import Graph, GraphError
from plexus.runtime import Engine, EngineError
from plexus.runtime.events import CallbackSink

log = logging.getLogger(__name__)

#: How long the pump gathers before sending. PROTOCOL.md suggests 20–50 ms; a
#: relay at speed produces far more telemetry than a browser wants as separate
#: messages, and one frame per tick is kinder to both ends.
BATCH_WINDOW_S = 0.025

#: Frames held before the trace starts thinning. Roughly a second of a fast
#: relay, which is more than a person can read and less than a memory problem.
QUEUE_LIMIT = 2000

PROTOCOL_VERSION = 1

Sender = Callable[[Dict[str, Any]], Awaitable[None]]


class Session:
    """The lifetime of one editor connection."""

    def __init__(self, send: Sender) -> None:
        self._send = send
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._dropped = 0
        self._lock = threading.Lock()
        self._pump: Optional[asyncio.Task] = None
        self.graph: Optional[Graph] = None
        self.engine = Engine(sink=CallbackSink(self._offer))

    # ── Frames out ──────────────────────────────────────────────────────

    def _offer(self, frame: Dict[str, Any]) -> None:
        """
        Called from whichever thread produced the frame, including the relay.

        Never blocks, never raises: the caller is in the middle of an exchange.
        """
        if self._queue.qsize() >= QUEUE_LIMIT:
            if frame.get("type") == "apdu":
                with self._lock:
                    self._dropped += 1
                return
            # A state or telemetry frame is worth making room for — losing one
            # leaves the editor's picture permanently wrong, not just thinner.
            self._drop_one_apdu()
        self._queue.put(frame)

    def _drop_one_apdu(self) -> None:
        held: List[Dict[str, Any]] = []
        dropped = False
        try:
            while True:
                frame = self._queue.get_nowait()
                if not dropped and frame.get("type") == "apdu":
                    dropped = True
                    continue
                held.append(frame)
        except queue.Empty:
            pass
        for frame in held:
            self._queue.put(frame)
        if dropped:
            with self._lock:
                self._dropped += 1

    async def start(self) -> None:
        self._pump = asyncio.ensure_future(self._run_pump())

    async def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            self._pump = None
        # Releasing the hardware matters more than a clean socket close, and
        # the engine's stop is blocking, so it goes to a worker.
        await asyncio.get_event_loop().run_in_executor(None, self.engine.stop)

    async def _run_pump(self) -> None:
        while True:
            await asyncio.sleep(BATCH_WINDOW_S)
            await self.flush()

    async def flush(self) -> None:
        """Send everything gathered since the last tick, as one frame if many."""
        frames: List[Dict[str, Any]] = []
        try:
            while True:
                frames.append(self._queue.get_nowait())
        except queue.Empty:
            pass

        with self._lock:
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
        await self._deliver(frames[0] if len(frames) == 1 else {"type": "batch", "frames": frames})

    async def _deliver(self, frame: Dict[str, Any]) -> None:
        try:
            await self._send(frame)
        except Exception:  # pragma: no cover - the socket went away
            log.debug("send failed; the editor has gone", exc_info=True)

    # ── Frames in ───────────────────────────────────────────────────────

    async def handle(self, message: Dict[str, Any]) -> None:
        """One frame from the editor."""
        kind = message.get("type")
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

        # Losing the socket is not losing the run, so a reconnecting editor is
        # told everything it needs to catch up rather than being asked to
        # restart something that is still going.
        await self._deliver({"type": "devices", "devices": []})
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
        loop = asyncio.get_event_loop()
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
        await asyncio.get_event_loop().run_in_executor(None, self.engine.stop)
        await self.flush()

    async def _command(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id")
        line = message.get("line")
        if not isinstance(request_id, str) or not isinstance(line, str):
            await self._deliver({"type": "error", "message": "command needs an id and a line."})
            return

        # `tx` reaches the card, so it runs off the event loop like everything
        # else that can block.
        lines, level = await asyncio.get_event_loop().run_in_executor(
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
    """Turn the editor's JSON node shape into the file's."""
    nodes = []
    for node in raw.get("nodes") or []:
        if not isinstance(node, dict):
            raise GraphError("A node in this graph is not an object.")
        position = node.get("position") or {}
        entry = {
            "id": node.get("id"),
            "type": node.get("type"),
            "at": [position.get("x", 0), position.get("y", 0)],
        }
        for key in ("label", "host"):
            if node.get(key):
                entry[key] = node[key]
        if node.get("disabled"):
            entry["disabled"] = True
        if node.get("config"):
            entry["config"] = node["config"]
        nodes.append(entry)

    edges = []
    for edge in raw.get("edges") or []:
        if not isinstance(edge, dict):
            raise GraphError("An edge in this graph is not an object.")
        source, target = edge.get("from") or {}, edge.get("to") or {}
        edges.append(
            {
                "id": edge.get("id"),
                "from": "%s:%s" % (source.get("node"), source.get("port")),
                "to": "%s:%s" % (target.get("node"), target.get("port")),
            }
        )

    return {
        "version": raw.get("version", 1),
        "name": raw.get("name") or "untitled",
        "hosts": raw.get("hosts") or [],
        "nodes": nodes,
        "edges": edges,
    }

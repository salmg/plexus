"""
What a running graph says about itself.

These are the frames in PROTOCOL.md, as Python. `to_frame` produces exactly the
JSON the editor's `ui/src/runtime/ws.ts` parses, so the WebSocket layer is a
transport and nothing more — it never has to know what an APDU is.

Sinks are deliberately dumb. The engine pushes; whoever is listening decides
whether that means a socket write, a line on a terminal, or a list in a test.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class NodeTelemetry:
    status: str = "idle"  # idle | ready | live | error
    apdus: int = 0
    errors: int = 0
    last_ms: Optional[float] = None
    mean_ms: Optional[float] = None
    detail: Optional[str] = None

    def to_frame(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "apdus": self.apdus,
            "errors": self.errors,
            "lastMs": _round(self.last_ms),
            "meanMs": _round(self.mean_ms),
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass
class EdgeTelemetry:
    apdus: int = 0
    last_ms: Optional[float] = None
    bytes_out: int = 0
    bytes_in: int = 0

    def to_frame(self) -> Dict[str, Any]:
        return {
            "apdus": self.apdus,
            "lastMs": _round(self.last_ms),
            "bytesOut": self.bytes_out,
            "bytesIn": self.bytes_in,
        }


@dataclass
class ApduEvent:
    seq: int
    t: float
    edge: str
    direction: str  # command | response
    hex: str
    ms: Optional[float] = None
    mutated_by: Optional[str] = None
    note: Optional[str] = None

    def to_frame(self) -> Dict[str, Any]:
        frame: Dict[str, Any] = {
            "seq": self.seq,
            "t": _round(self.t),
            "edge": self.edge,
            "dir": self.direction,
            "hex": self.hex,
        }
        if self.ms is not None:
            frame["ms"] = _round(self.ms)
        if self.mutated_by:
            frame["mutatedBy"] = self.mutated_by
        if self.note:
            frame["note"] = self.note
        return frame


@dataclass
class LogLine:
    t: float
    level: str  # debug | info | warn | error
    source: str
    text: str

    def to_frame(self) -> Dict[str, Any]:
        return {
            "t": _round(self.t),
            "level": self.level,
            "source": self.source,
            "text": self.text,
        }


class Sink:
    """
    Somewhere for events to go.

    The base collects them, which is all a test needs. The WebSocket server
    subclasses it to batch and write. Nothing here blocks: a slow listener must
    not be able to stall the relay, because the relay is holding a card.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.states: List[Dict[str, Any]] = []
        self.nodes: Dict[str, NodeTelemetry] = {}
        self.edges: Dict[str, EdgeTelemetry] = {}
        self.apdus: List[ApduEvent] = []
        self.logs: List[LogLine] = []

    # ── What the engine calls ───────────────────────────────────────────

    def state(self, state: str, detail: Optional[str] = None) -> None:
        with self._lock:
            self.states.append({"state": state, "detail": detail})
        self.emit(
            {"type": "state", "state": state, **({"detail": detail} if detail else {})}
        )

    def node(self, node_id: str, telemetry: NodeTelemetry) -> None:
        with self._lock:
            self.nodes[node_id] = telemetry
        self.emit({"type": "node", "id": node_id, "telemetry": telemetry.to_frame()})

    def edge(self, edge_id: str, telemetry: EdgeTelemetry) -> None:
        with self._lock:
            self.edges[edge_id] = telemetry
        self.emit({"type": "edge", "id": edge_id, "telemetry": telemetry.to_frame()})

    def apdu(self, event: ApduEvent) -> None:
        with self._lock:
            self.apdus.append(event)
        self.emit({"type": "apdu", "event": event.to_frame()})

    def log(self, line: LogLine) -> None:
        with self._lock:
            self.logs.append(line)
        self.emit({"type": "log", "line": line.to_frame()})

    # ── What a subclass overrides ───────────────────────────────────────

    def emit(self, frame: Dict[str, Any]) -> None:
        """One frame, ready for the wire. The base does nothing with it."""


class CallbackSink(Sink):
    """A sink that hands every frame to a function. Used by the server."""

    def __init__(self, on_frame: Callable[[Dict[str, Any]], None]) -> None:
        super().__init__()
        self._on_frame = on_frame

    def emit(self, frame: Dict[str, Any]) -> None:
        self._on_frame(frame)


class Clock:
    """Milliseconds since the run started. One per run, shared by everything."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def reset(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0


@dataclass
class RollingMean:
    """
    Mean of the last `window` samples.

    A running average over a whole session hides the thing you are watching
    for — a card that got slower ten seconds ago. A short window shows it.
    """

    window: int = 50
    samples: List[float] = field(default_factory=list)

    def add(self, value: float) -> float:
        self.samples.append(value)
        if len(self.samples) > self.window:
            del self.samples[0]
        return self.mean

    @property
    def mean(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else 0.0


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)

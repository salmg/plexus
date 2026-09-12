"""
Card-side nodes — the things that answer.

Only one of these runs without hardware, and that one carries the test suite:
a recorded capture, replayed by atrium's own transport. The rest name what they
need and refuse clearly, because a card node that silently pretends is the
worst possible failure in a tool whose output is evidence.
"""

from __future__ import annotations

from typing import Any, Dict

from plexus.adapters import load_recorded_card
from plexus.nodes.paths import resolve_read


class CardUnavailable(RuntimeError):
    """This card node cannot be bound here. User-facing, and says why."""


class RecordedCard:
    """
    A card that answers from a capture file.

    What it cannot do is answer a challenge it has never seen: a terminal picks
    its own unpredictable number, and the cryptogram in the capture was computed
    over a different one. Everything before GENERATE AC replays honestly, and
    atrium logs every fallback match, so a response served by a looser rule than
    exact match is visible as the weaker claim it is.
    """

    kind = "capture"

    def __init__(self, path: str, strict: bool = True) -> None:
        if not path:
            raise CardUnavailable("This capture node has no file selected.")
        # Confined for the same reason the trace node's path is: a graph names
        # this file and a graph is untrusted input. Pointing a card node at
        # something that is not a capture is a read of that file either way.
        self.path = resolve_read(path, what="capture")
        self._inner = load_recorded_card(self.path, strict=strict)

    def connect(self) -> None:
        self._inner.connect()

    def get_atr(self) -> bytes:
        return self._inner.get_atr()

    def transmit(self, apdu: bytes) -> bytes:
        return self._inner.transmit(apdu)

    def disconnect(self) -> None:
        self._inner.disconnect()


class NotHereYet:
    """
    A card node whose implementation needs hardware or a rig.

    It exists so the graph still resolves and the engine can say exactly what
    is missing at bind time, rather than the node simply not being in the
    registry and the failure reading as a corrupt graph.
    """

    def __init__(self, node_type: str, needs: str) -> None:
        self.node_type = node_type
        self.needs = needs

    def connect(self) -> None:
        raise CardUnavailable(
            f"The {self.node_type} node is not implemented in this build. It needs {self.needs}."
        )

    def get_atr(self) -> bytes:
        self.connect()
        return b""

    def transmit(self, apdu: bytes) -> bytes:
        self.connect()
        return b""

    def disconnect(self) -> None:
        pass


#: What each unimplemented card node is waiting on. Kept as data so the message
#: is the same everywhere and the list is easy to read against the catalogue.
PENDING = {
    "pcsc": "pyscard and a reader attached to this machine",
    "acr122u.reader": "pyscard, an ACR122U, and atrium's contactless transport",
    "nfcgate": "atrium's NFCGate transport and a reachable server",
    "cardproxy": "atrium's remote transport and a running card proxy",
}


def build(node_type: str, config: Dict[str, Any]):
    """Make the card-side node a catalogue key names."""
    if node_type == "capture":
        return RecordedCard(
            str(config.get("path") or ""),
            strict=bool(config.get("strict", True)),
        )
    if node_type in PENDING:
        return NotHereYet(node_type, PENDING[node_type])
    raise CardUnavailable(f"No card implementation for {node_type!r}.")

"""
Card-side nodes — the things that answer.

Only one of these runs without hardware, and that one carries the test suite:
a recorded capture, replayed by atrium's own transport. The rest name what they
need and refuse clearly, because a card node that silently pretends is the
worst possible failure in a tool whose output is evidence.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from plexus.adapters import atrium as _atrium
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


def _reader_spec(device: str) -> Optional[str]:
    """
    Turn the device id a graph stores into the reader spec atrium resolves.

    The runtime hands the editor readers as ``pcsc:<pc/sc name>`` (see
    `plexus.runtime.engine.discover_devices`), and that id is what the graph
    saves. Everything after the prefix is the reader's own name, which atrium
    matches exactly or as a substring. An empty selection is ``None``: "pick
    the sensible default", which is what atrium does with it.
    """
    device = (device or "").strip()
    if not device:
        return None
    prefix = "pcsc:"
    return device[len(prefix):] if device.startswith(prefix) else device


class PcscCard:
    """
    A contact card in a PC/SC reader, bound through atrium's local transport.

    Constructing it touches no hardware — it only picks the reader. The card is
    opened at `connect`, which is where a missing reader or an empty slot is
    refused, with atrium's own message. That matches the engine, which binds
    every node and then connects the card before it calls anything a run.
    """

    kind = "pcsc"

    def __init__(self, device: str = "") -> None:
        self.reader = _reader_spec(device)
        try:
            self._inner = _atrium.open_local_card(self.reader)
        except _atrium.AtriumUnavailable as exc:
            raise CardUnavailable(str(exc)) from exc

    def connect(self) -> None:
        try:
            self._inner.connect()
        except Exception as exc:  # noqa: BLE001 — atrium/pyscard raise several
            raise CardUnavailable(str(exc)) from exc

    def get_atr(self) -> bytes:
        return bytes(self._inner.get_atr())

    def transmit(self, apdu: bytes) -> bytes:
        return bytes(self._inner.transmit(apdu))

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
            f"The {self.node_type} node is not implemented in this build. "
            f"It needs {self.needs}."
        )

    def get_atr(self) -> bytes:
        self.connect()
        return b""

    def transmit(self, apdu: bytes) -> bytes:
        self.connect()
        return b""

    def disconnect(self) -> None:
        pass


#: Card nodes with no implementation in this build at all. Kept as data so the
#: message is the same everywhere and the list reads against the catalogue.
#:
#: `pcsc` is deliberately *not* here: it is implemented (`PcscCard`), and
#: whether it can bind is a fact about the machine — a reader is plugged in or
#: it is not — rather than about how the build was made. `pending()` answers
#: that, the way terminal.py's does for the agent.
PENDING = {
    "acr122u.reader": "pyscard, an ACR122U, and atrium's contactless transport",
    "nfcgate": "atrium's NFCGate transport and a reachable server",
    "cardproxy": "atrium's remote transport and a running card proxy",
}

#: What the pcsc node is waiting on, when it is waiting on anything.
_PCSC_NEEDS = "pyscard and a reader attached to this machine"


def pcsc_pending_reason() -> Optional[str]:
    """Why the pcsc node cannot bind here, or None when a reader is present."""
    if not _atrium.atrium_present():
        return _PCSC_NEEDS
    if not _atrium.pcsc_readers():
        return _PCSC_NEEDS
    return None


def pending() -> Dict[str, str]:
    """
    The static gaps, plus pcsc when no reader is attached here.

    A function rather than the `PENDING` constant, because pcsc's answer
    depends on the machine the runtime is on, not on how the build was made —
    the same reason terminal.py has one.
    """
    out = dict(PENDING)
    reason = pcsc_pending_reason()
    if reason is not None:
        out["pcsc"] = reason
    return out


def build(node_type: str, config: Dict[str, Any]):
    """Make the card-side node a catalogue key names."""
    if node_type == "capture":
        return RecordedCard(
            str(config.get("path") or ""),
            strict=bool(config.get("strict", True)),
        )
    if node_type == "pcsc":
        return PcscCard(str(config.get("device") or ""))
    if node_type in PENDING:
        return NotHereYet(node_type, PENDING[node_type])
    raise CardUnavailable(f"No card implementation for {node_type!r}.")

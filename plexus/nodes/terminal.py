"""
Terminal-side nodes — the things that ask.

A terminal owns the loop. The engine hands it a chain that behaves like a card
and then stays out of the way, because that is what a terminal is: the party
that decides when to send and what to send next.

One of these runs without hardware — the fingerprint probe — and it is what
turns "a capture and some transforms" into a graph that actually does
something. The rest present a card to a real terminal, which is not a thing
software can do alone.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)


class TerminalUnavailable(RuntimeError):
    """This terminal node cannot be bound here. User-facing, and says why."""


# ── A little BER-TLV, enough to steer a probe ───────────────────────────────


def tlv_walk(data: bytes) -> Iterator[Tuple[int, bytes]]:
    """
    Yield (tag, value) for every TLV in `data`, descending into constructed
    ones.

    Deliberately forgiving: a probe reads whatever a card chose to send, and
    bailing out on the first surprising byte would lose the records before it.
    Anything that does not parse is skipped rather than raised.
    """
    i = 0
    end = len(data)
    while i < end:
        first = data[i]
        if first in (0x00, 0xFF):  # padding between records
            i += 1
            continue

        tag = first
        i += 1
        if first & 0x1F == 0x1F:  # multi-byte tag
            while i < end:
                tag = (tag << 8) | data[i]
                more = data[i] & 0x80
                i += 1
                if not more:
                    break

        if i >= end:
            return
        length = data[i]
        i += 1
        if length & 0x80:
            count = length & 0x7F
            if count == 0 or i + count > end:
                return
            length = int.from_bytes(data[i : i + count], "big")
            i += count

        if i + length > end:
            return
        value = data[i : i + length]
        i += length

        yield tag, value
        if first & 0x20:  # constructed
            yield from tlv_walk(value)


def find_all(data: bytes, tag: int) -> List[bytes]:
    return [value for found, value in tlv_walk(data) if found == tag]


SW_OK = b"\x90\x00"


def succeeded(response: bytes) -> bool:
    return len(response) >= 2 and response[-2:] == SW_OK


# ── The probe ───────────────────────────────────────────────────────────────

PPSE = bytes.fromhex("00A404000E325041592E5359532E444446303100")


class FingerprintProbe:
    """
    Runs a discovery sequence and records what the card answered.

    Not a payment: it selects the directory, selects whatever applications the
    card advertises, asks for processing options and reads records. That is the
    part of a transaction that tells you what a card *is*, and it is the part a
    recorded capture can answer honestly.
    """

    DEPTHS = ("quick", "standard", "deep")

    def __init__(self, depth: str = "standard") -> None:
        if depth not in self.DEPTHS:
            raise TerminalUnavailable(
                f"Probe depth is one of {', '.join(self.DEPTHS)}; got {depth!r}."
            )
        self.depth = depth
        self.aids: List[bytes] = []
        self.findings: Dict[str, Any] = {}
        self.sent = 0

    def drive(self, channel, stop: Callable[[], bool]) -> None:
        ask = _asker(channel, stop, self)

        directory = ask(PPSE)
        if directory is None:
            return
        if not succeeded(directory):
            log.info("no PPSE on this card (%s); trying known AIDs", directory[-2:].hex())

        self.aids = find_all(directory, 0x4F) or list(_WELL_KNOWN_AIDS)
        self.findings["aids"] = [a.hex().upper() for a in self.aids]

        for aid in self.aids:
            if stop():
                return
            fci = ask(select_aid(aid))
            if fci is None:
                return
            if not succeeded(fci):
                continue
            self.findings.setdefault("selected", []).append(aid.hex().upper())

            if self.depth == "quick":
                continue

            options = ask(gpo_for(fci))
            if options is None:
                return
            if succeeded(options):
                for command in read_records(options, deep=self.depth == "deep"):
                    if stop():
                        return
                    if ask(command) is None:
                        return

    def note(self, command: bytes, response: bytes) -> None:
        self.sent += 1


def _asker(channel, stop, probe: "FingerprintProbe"):
    """One place that sends, counts, and honours the stop flag."""

    def ask(command: bytes) -> Optional[bytes]:
        if stop():
            return None
        response = channel.transmit(command)
        probe.note(command, response)
        return response

    return ask


#: Tried only when a card has no PPSE — contact cards often do not.
_WELL_KNOWN_AIDS = (
    bytes.fromhex("A0000000031010"),  # Visa
    bytes.fromhex("A0000000041010"),  # Mastercard
    bytes.fromhex("A0000000250109"),  # Amex
)


def select_aid(aid: bytes) -> bytes:
    return bytes([0x00, 0xA4, 0x04, 0x00, len(aid)]) + aid + b"\x00"


def gpo_for(fci: bytes) -> bytes:
    """
    Build GET PROCESSING OPTIONS for whatever PDOL the card asked for.

    Every requested element is sent as zeroes of the right length. A probe is
    not trying to be approved — it is trying to get a well-formed answer, and
    zeroes are the least surprising thing to send.
    """
    pdol = next(iter(find_all(fci, 0x9F38)), b"")
    filler = bytes(_pdol_length(pdol))
    data = b"\x83" + bytes([len(filler)]) + filler
    return bytes([0x80, 0xA8, 0x00, 0x00, len(data)]) + data + b"\x00"


def _pdol_length(pdol: bytes) -> int:
    """Total bytes a PDOL asks for. A DOL is tag/length pairs with no values."""
    total = 0
    i = 0
    while i < len(pdol):
        first = pdol[i]
        i += 1
        if first & 0x1F == 0x1F:
            while i < len(pdol) and pdol[i] & 0x80:
                i += 1
            i += 1
        if i >= len(pdol):
            break
        total += pdol[i]
        i += 1
    return total


def read_records(gpo_response: bytes, deep: bool = False) -> List[bytes]:
    """
    READ RECORD commands for the AFL the card returned.

    The AFL (tag 94) is four-byte entries: SFI in the top five bits, then first
    record, last record, and how many are signed. Without one there is nothing
    to read, and a sweep is only worth it at deep.
    """
    commands: List[bytes] = []
    afl = next(iter(find_all(gpo_response, 0x94)), b"")

    for i in range(0, len(afl) - 3, 4):
        sfi = afl[i] >> 3
        first, last = afl[i + 1], afl[i + 2]
        if sfi == 0 or first == 0 or last < first:
            continue
        for record in range(first, min(last, first + 15) + 1):
            commands.append(bytes([0x00, 0xB2, record, (sfi << 3) | 0x04, 0x00]))

    if deep and not commands:
        for sfi in range(1, 11):
            for record in range(1, 5):
                commands.append(bytes([0x00, 0xB2, record, (sfi << 3) | 0x04, 0x00]))

    return commands


# ── The rest ────────────────────────────────────────────────────────────────


class NotHereYet:
    """A terminal node that needs hardware, or a daemon started by hand."""

    def __init__(self, node_type: str, needs: str) -> None:
        self.node_type = node_type
        self.needs = needs

    def drive(self, channel, stop) -> None:
        raise TerminalUnavailable(
            f"The {self.node_type} node is not implemented in this build. "
            f"It needs {self.needs}."
        )


PENDING = {
    "acr122u.target": "pyscard, an ACR122U, and atrium's PN532 emulator",
    "simtrace2": "a SimTrace2 and simtrace2-remsim running in a root shell",
    "vpcd": "virtualsmartcard, with vpcd loaded into pcscd",
    "agent": "a configured model back end and atrium's agent loop",
}


def build(node_type: str, config: Dict[str, Any]):
    """Make the terminal-side node a catalogue key names."""
    if node_type == "probe":
        return FingerprintProbe(depth=str(config.get("depth") or "standard"))
    if node_type in PENDING:
        return NotHereYet(node_type, PENDING[node_type])
    raise TerminalUnavailable(f"No terminal implementation for {node_type!r}.")

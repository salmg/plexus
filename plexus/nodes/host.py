"""
Host-layer nodes — the conversation above the card.

Where the card side is terminal ⇄ card speaking APDUs, this is
acquirer ⇄ gateway ⇄ issuer speaking ISO 8583. Atrium has the whole of it in
`host/`: framing, dialects, pack and unpack, a passive relay, a playbook-driven
mutating proxy, corpus replay, and cryptogram verification. None of it imports
pyscard, so it runs on a machine with no reader.

**None of these bind yet, and the reason is not a missing adapter.** The engine
drives one shape of conversation — the card's `transmit(command) -> response`,
where a terminal owns the loop and every step is a round trip. A host link is
`send` and `receive` over a framed socket: messages are not strictly paired,
either end may speak first, and a response can arrive for a request two ahead.
That is a second driving model beside the first, not another `CardTransport`,
so the nodes resolve, check and save while the engine work is a separate piece.

They are here rather than absent for the reason the hardware nodes are: a graph
that names a node the build does not have should say so, not look corrupt.
"""

from __future__ import annotations

from typing import Any, Dict


class HostUnavailable(RuntimeError):
    """This host-layer node cannot be bound here. User-facing, and says why."""


#: Every host-layer node, and what each is waiting on.
#:
#: All three wait on the same thing today, said three ways so the sentence names
#: the node's own job rather than the engine's internals.
PENDING = {
    "iso8583.replay": (
        "an engine that drives the host layer — atrium's corpus replay is "
        "there, the send/receive loop beside the card's is not"
    ),
    "iso8583.host": (
        "an engine that drives the host layer, and a reachable acquirer, "
        "gateway or issuer to point it at"
    ),
    "iso8583.mutation": (
        "an engine that drives the host layer — atrium's host mutation engine "
        "is there, the conversation for it to sit in is not"
    ),
}


def build(node_type: str, config: Dict[str, Any]):
    """
    Make the host-layer node a catalogue key names.

    Refused here, at bind, for the same reason the card and terminal sides
    refuse here: a node that fails on the relay thread instead reports a run in
    progress and then faults with a card already open.
    """
    if node_type in PENDING:
        raise HostUnavailable(
            "The %s node is not implemented in this build. It needs %s."
            % (node_type, PENDING[node_type])
        )
    raise HostUnavailable(f"No host-layer implementation for {node_type!r}.")

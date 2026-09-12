"""
Thin wrappers over the atrium modules Plexus actually uses.

Two things worth knowing before adding to this file.

**Atrium splits cleanly in half, and only one half needs hardware.**
``mutation_engine``, ``transport.recorded``, ``emv_logger`` and
``card_fingerprint`` are pure standard library: they import on any machine, with
no reader attached and no pyscard installed.  ``transport.local``,
``transport.contactless`` and the emulator are the other half.  So a graph made
of a recorded capture and some transforms runs anywhere, and that is what the
test suite exercises.

**The contracts line up already.**  ``CardTransport`` is connect / get_atr /
transmit / disconnect, which is exactly what a card-side node has to be.
``MutationEngine`` is ``on_command(cmd) -> cmd`` and
``on_response(cmd, resp) -> resp``, which is exactly a transform.  Plexus did
not invent these shapes; it found them.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

#: The submodule, two levels up from this file.
VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor" / "atrium"


class AtriumUnavailable(RuntimeError):
    """Atrium is not where it should be, or a piece of it will not import."""


def atrium_path() -> Path:
    return VENDOR


@lru_cache(maxsize=1)
def require_atrium() -> Path:
    """
    Put atrium on ``sys.path`` and confirm it is really there.

    Cached, because doing it twice would put the same directory on the path
    twice.  The marker file is checked rather than the directory, because an
    uninitialised submodule is an empty directory that exists.
    """
    marker = VENDOR / "transport" / "base.py"
    if not marker.is_file():
        raise AtriumUnavailable(
            f"No atrium checkout at {VENDOR}.\n"
            "It is a submodule — fetch it with:\n"
            "    git submodule update --init vendor/atrium"
        )

    path = str(VENDOR)
    if path not in sys.path:
        # Appended, not inserted: atrium has modules with plain names like
        # `util` and `transport`, and putting those ahead of the standard
        # library or the venv would be a trap set for some unrelated import.
        sys.path.append(path)
    return VENDOR


def load_recorded_card(path: str | Path, strict: bool = False) -> Any:
    """
    A card that answers from a capture file.

    This is the one card-side implementation that needs no hardware, which
    makes it the one the whole engine can be tested against.
    """
    require_atrium()
    try:
        from transport.recorded import RecordedCardError, RecordedCardTransport
    except ImportError as exc:  # pragma: no cover - only if atrium moves
        raise AtriumUnavailable(
            f"atrium's recorded transport will not import: {exc}"
        ) from exc

    try:
        return RecordedCardTransport(str(path), strict=strict)
    except RecordedCardError as exc:
        # Atrium's own message names the file and what was wrong with it, which
        # is better than anything this layer could say.
        raise AtriumUnavailable(str(exc)) from exc


def load_mutation_engine(config_path: str | Path, card_execute=None) -> Any:
    """
    Atrium's mutation engine, configured from a playbook on disk.

    ``card_execute`` is the out-of-band line to the card that injected commands
    travel down — the dashed edge on the canvas.  Passing ``None`` disables
    injection rather than failing, because a playbook with no injections in it
    should not need a card to be bound first.
    """
    require_atrium()
    try:
        from mutation_engine import MutationEngine
    except ImportError as exc:  # pragma: no cover - only if atrium moves
        raise AtriumUnavailable(
            f"atrium's mutation engine will not import: {exc}"
        ) from exc

    holder = _ExecuteHolder(card_execute) if card_execute else None
    return MutationEngine.from_config(str(config_path), os=holder)


class _ExecuteHolder:
    """
    Adapts a plain callable to the ``.execute(apdu)`` object atrium expects.

    ``MutationEngine.from_config`` takes a card *OS* and calls ``os.execute``;
    Plexus has a function. One small class beats changing atrium.
    """

    __slots__ = ("_call",)

    def __init__(self, call) -> None:
        self._call = call

    def execute(self, apdu):
        return self._call(apdu)

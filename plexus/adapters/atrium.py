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

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Where an atrium checkout might be, best guess first.
#:
#: It used to be one path — the submodule, relative to this file — which is
#: right for a clone and wrong for everything else. `pip install plexus` puts
#: the package under site-packages, `vendor/` is not inside the package, and no
#: wheel carries it: the installed `plexus` could read a graph and not run one,
#: with an error naming a directory inside site-packages that nobody would ever
#: create.
#:
#: Atrium is not vendored into the wheel because it is a separate project with
#: its own history and its own licence to state. So it is looked for instead,
#: and `PLEXUS_ATRIUM` is the answer when it lives somewhere unusual.
ATRIUM_VARIABLE = "PLEXUS_ATRIUM"


def atrium_candidates() -> list[Path]:
    """Every place a checkout could be, in the order they are tried."""
    here = Path(__file__).resolve().parent.parent.parent
    cwd = Path.cwd()

    out: list[Path] = []
    configured = os.environ.get(ATRIUM_VARIABLE)
    if configured:
        out.append(Path(configured).expanduser())
    out += [
        here / "vendor" / "atrium",  # a clone of this repository
        cwd / "vendor" / "atrium",  # run from the root of one
        cwd / "atrium",  # a checkout beside the working directory
        Path.home() / "atrium",
    ]

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in out:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


#: The first candidate, for a message that has to name one.
VENDOR = atrium_candidates()[0]


def atrium_present() -> bool:
    """
    Is there an atrium checkout to import, without importing it?

    A plain filesystem check, no ``sys.path`` mutation and nothing cached — so
    it is safe to call from an availability probe that runs on every graph
    edit, where ``require_atrium`` (which does mutate the path) would not be.
    """
    return any((p / "transport" / "base.py").is_file() for p in atrium_candidates())


class AtriumUnavailable(RuntimeError):
    """Atrium is not where it should be, or a piece of it will not import."""


@lru_cache(maxsize=1)
def require_atrium() -> Path:
    """
    Put atrium on ``sys.path`` and confirm it is really there.

    Cached, because doing it twice would put the same directory on the path
    twice.  The marker file is checked rather than the directory, because an
    uninitialised submodule is an empty directory that exists.
    """
    found = next(
        (p for p in atrium_candidates() if (p / "transport" / "base.py").is_file()),
        None,
    )
    if found is None:
        looked = "\n".join("    %s" % p for p in atrium_candidates())
        raise AtriumUnavailable(
            "No atrium checkout found. Looked in:\n"
            f"{looked}\n\n"
            "Inside a clone of this repository:\n"
            "    git submodule update --init vendor/atrium\n\n"
            "Anywhere else, point at one:\n"
            f"    {ATRIUM_VARIABLE}=/path/to/atrium"
        )

    path = str(found)
    if path not in sys.path:
        # Appended, not inserted: atrium has modules with plain names like
        # `util` and `transport`, and putting those ahead of the standard
        # library or the venv would be a trap set for some unrelated import.
        sys.path.append(path)
    return found


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


def pcsc_readers() -> list[dict]:
    """
    The PC/SC readers atrium can see on this machine, newest question each call.

    Each entry is ``{"index", "name", "kind", "virtual", "pn532"}`` — atrium's
    own ``Reader.to_dict()``.  The list is empty when there is no atrium
    checkout, no pyscard, or no reader plugged in: the ordinary states of a
    machine running only half this toolkit.

    It never raises.  Discovery runs on a poll and beside the editor, so an
    exception here would be worse than an empty list on a laptop with nothing
    attached — the reader simply does not appear, which is the truth.
    """
    try:
        require_atrium()
        from core.readers import probe
    except (AtriumUnavailable, ImportError):
        return []
    try:
        readers, _problem = probe()  # probe() is documented never to raise
    except Exception:  # noqa: BLE001 — belt and braces around a third party
        return []
    return [r.to_dict() for r in readers]


def open_local_card(reader: "int | str | None") -> Any:
    """
    Atrium's local PC/SC transport, addressed by index or name fragment.

    The object returned is the ``CardTransport`` shape a card-side node needs —
    connect / get_atr / transmit / disconnect — so Plexus never imports an
    atrium type to hold it.  ``None`` means "pick the sensible default", which
    is what atrium's resolver does.  Constructing it touches no hardware; the
    reader is opened at ``connect()``.
    """
    require_atrium()
    try:
        from transport.local import LocalCardTransport
    except ImportError as exc:  # pragma: no cover - only if atrium moves
        raise AtriumUnavailable(
            f"atrium's local transport will not import: {exc}"
        ) from exc
    return LocalCardTransport(reader)


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


def resolve_llm_provider(name: str = "", model: str = "") -> Any:
    """
    Atrium's language-model provider, chosen by name and configured from the
    environment.

    The one door to the AI agent. ``name`` is a catalogue value —
    ``anthropic``, ``openai`` or ``local`` — and an empty one lets atrium detect
    whichever key is set. Atrium raises ``ProviderUnavailable`` with a message
    that already names the exact variable to set, so it is worth more than
    anything this layer could write; it is re-raised verbatim as
    ``AtriumUnavailable`` so callers above never import an atrium type.
    """
    require_atrium()
    try:
        from llm_provider import (  # type: ignore[import-not-found]
            ProviderError,
            resolve_provider,
        )
    except ImportError as exc:  # pragma: no cover - only if atrium moves
        raise AtriumUnavailable(
            f"atrium's llm_provider will not import: {exc}"
        ) from exc

    try:
        return resolve_provider(name or None, model or None)
    except ProviderError as exc:
        # ProviderUnavailable (no key/SDK) and ProviderError (a bad request)
        # both land here; the message is atrium's and is user-facing.
        raise AtriumUnavailable(str(exc)) from exc

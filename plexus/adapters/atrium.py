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

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

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


#: What to run when the reader stack is missing a piece. One table, so the
#: picker, the console, `plexus nodes` and the palette all say the same thing.
PYSCARD_INSTALL = 'pip install -e ".[hardware]"'
PCSC_LINUX_PACKAGES = "sudo apt install pcscd libpcsclite-dev swig python3-dev"


@dataclass(frozen=True)
class ReaderDiscovery:
    """
    The readers a card can sit in, and why there are none when there are none.

    ``readers`` leaves out atrium's virtual reader: it is atrium's own output
    side, the thing presenting a card to a terminal, so offering it as a slot
    would be the one wiring mistake ``core.readers`` exists to prevent.

    ``problem`` is a sentence for a person, empty when a reader is there.
    ``needs`` is the same answer as a noun phrase, the shape the palette and
    ``node_requirements`` read ("Needs <needs>."), empty likewise.

    The empty list used to be the whole answer. A missing atrium checkout, a
    pyscard installed into a different Python, and a pcscd refusing this user
    all looked exactly like a machine with nothing plugged in, while
    ``pcsc_scan`` listed the reader in the next terminal.
    """

    readers: list = field(default_factory=list)
    problem: str = ""
    needs: str = ""


def pcsc_discovery() -> ReaderDiscovery:
    """
    The card readers on this machine, asked fresh each call. Never raises.

    Discovery runs beside the editor and on every graph edit, so an exception
    here would be worse than an answer saying what is missing.
    """
    if not atrium_present():
        return ReaderDiscovery(
            problem=(
                "atrium is not checked out, so no reader can be listed. Inside a "
                "clone of this repository: git submodule update --init "
                "vendor/atrium (a ZIP download never includes it), or set %s "
                "to an atrium checkout." % ATRIUM_VARIABLE
            ),
            needs="the atrium submodule (git submodule update --init vendor/atrium)",
        )

    # Asked here rather than read out of atrium's own message, which says
    # `pip install pyscard` and nothing about which Python. The usual way to
    # get here with pyscard "installed" is to have installed it into another
    # interpreter than the one running Plexus.
    if importlib.util.find_spec("smartcard") is None:
        return ReaderDiscovery(
            problem=(
                "pyscard is not installed in the Python running Plexus (%s), so "
                "no PC/SC reader can be opened. Install it into that environment: "
                "%s. On Linux pyscard builds from source and needs the PC/SC "
                "headers and swig first: %s."
                % (sys.executable, PYSCARD_INSTALL, PCSC_LINUX_PACKAGES)
            ),
            needs="pyscard in this Python (%s)" % PYSCARD_INSTALL,
        )

    try:
        require_atrium()
        from core.readers import probe
    except (AtriumUnavailable, ImportError) as exc:
        return ReaderDiscovery(
            problem="atrium's reader discovery will not import: %s" % exc,
            needs="atrium's core.readers to import",
        )
    try:
        found, problem = probe()  # probe() is documented never to raise
    except Exception as exc:  # noqa: BLE001 — belt and braces around a third party
        found, problem = [], "Listing readers failed: %s" % exc

    readers = [r.to_dict() for r in found]
    slots = [r for r in readers if not r.get("virtual")]
    if slots:
        return ReaderDiscovery(readers=slots)
    if readers:
        names = ", ".join(r["name"] for r in readers)
        return ReaderDiscovery(
            problem=(
                "Only atrium's virtual reader is visible (%s). That is where atrium "
                "presents a card to a terminal, not a slot a card sits in. Plug a "
                "reader in." % names
            ),
            needs="a reader attached to this machine",
        )
    return ReaderDiscovery(
        # atrium's own words: "No PC/SC readers found…" or "Could not list
        # readers (…) — is pcscd running?", which name the cause better than
        # anything this layer could.
        problem=problem or "No PC/SC readers found.",
        needs="a reader attached to this machine, with pcscd running",
    )


def pcsc_readers() -> list[dict]:
    """
    The card readers alone, each atrium's own ``Reader.to_dict()``:
    ``{"index", "name", "kind", "virtual", "pn532"}``, virtual reader left out.
    """
    return pcsc_discovery().readers


# ── SimTrace2 ────────────────────────────────────────────────────────────────
#
# The chain a SimTrace2 relay runs through, none of it Plexus's to start:
#
#   terminal ─► SimTrace2 board ─► simtrace2-remsim ─► PC/SC reader 0
#            = vpcd's "Virtual PCD" ─► a vpicc on port 35963 ─► the chain
#
# The daemon needs raw USB, so the operator starts it in a root shell; atrium
# refuses to launch it and so does Plexus. What is asked of atrium here is the
# discovery it already does for its own dashboard (which board, is a daemon up
# for it, what command would start one) and the vpicc side: `VirtualCard`,
# which atrium's own relay hands an OS and an attack to.

#: What simtrace2-remsim talks to. atrium binds the same address and port.
VPCD_HOST = "127.0.0.1"
VPCD_PORT = 35963


@dataclass(frozen=True)
class SimTraceDiscovery:
    """
    The SimTrace2 boards on this machine and the daemon serving each.

    ``boards`` are atrium's own dicts (``usb_path``, ``manufacturer``,
    ``product``…) with ``daemon`` added: the running simtrace2-remsim for that
    board, or None. ``problem`` and ``needs`` read as `ReaderDiscovery`'s do,
    and are empty when a board has a daemon up, which is the state a relay can
    run in.
    """

    boards: list = field(default_factory=list)
    problem: str = ""
    needs: str = ""


def _simtrace_module() -> Any:
    """
    atrium's ``api/routes/simtrace.py``, loaded on its own.

    By file rather than ``import api.routes.simtrace``: that would run
    ``api/__init__.py``, which builds atrium's whole web server, and all that
    is wanted is two functions that read /sys and /proc. The module still needs
    fastapi and pydantic at import (it declares its routes), which the
    ``runtime`` extra brings.
    """
    import importlib.util as _util

    root = require_atrium()
    path = root / "api" / "routes" / "simtrace.py"
    spec = _util.spec_from_file_location("_plexus_atrium_simtrace", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no module at {path}")
    module = _util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def simtrace_discovery() -> SimTraceDiscovery:
    """The SimTrace2 boards here, asked fresh each call. Never raises."""
    needs_rig = "a SimTrace2 on USB with simtrace2-remsim running for it"
    if not sys.platform.startswith("linux"):
        return SimTraceDiscovery(
            problem=(
                "SimTrace2 runs on Linux: the board is found in /sys, its daemon "
                "in /proc, and simtrace2-remsim and vpcd are Linux tools. You can "
                "draw and check this graph here; arm it on the machine the board "
                "is plugged into."
            ),
            needs="Linux, " + needs_rig,
        )
    if not atrium_present():
        return SimTraceDiscovery(
            problem=(
                "atrium is not checked out, so no SimTrace2 can be found. Inside a "
                "clone of this repository: git submodule update --init vendor/atrium."
            ),
            needs="the atrium submodule (git submodule update --init vendor/atrium)",
        )
    try:
        st = _simtrace_module()
        boards = list(st.list_devices().get("devices", []))
        daemons = list(st.find_remsim_processes())
    except Exception as exc:  # noqa: BLE001 — an import or a /proc read
        return SimTraceDiscovery(
            problem=(
                "atrium's SimTrace2 discovery did not run: %s. It needs the runtime "
                'extra (pip install -e ".[runtime]").' % exc
            ),
            needs=needs_rig,
        )

    for board in boards:
        board["daemon"] = next(
            (d for d in daemons if d.get("usb_path") == board.get("usb_path")), None
        )

    if not boards:
        elsewhere = ""
        if daemons:
            elsewhere = (
                " A simtrace2-remsim is running for %s, which is not a board this "
                "machine can see." % (daemons[0].get("usb_path") or "an unnamed device")
            )
        return SimTraceDiscovery(
            problem=(
                "No SimTrace2 on USB (vendor 1d50, product 60e3). Plug the board "
                "in, and check that this VM or container is given the USB "
                "device.%s" % elsewhere
            ),
            needs=needs_rig,
        )

    if not any(b["daemon"] for b in boards):
        try:
            command = st.simtrace_command(boards[0].get("usb_path", ""))["command"]
        except Exception:  # noqa: BLE001 — the hint is a courtesy
            command = "sudo simtrace2-remsim --usb-path %s" % boards[0].get("usb_path")
        return SimTraceDiscovery(
            boards=boards,
            problem=(
                "The SimTrace2 at %s is plugged in, but no simtrace2-remsim is "
                "running for it. Start it in a root shell; Plexus never launches "
                "it: %s" % (boards[0].get("usb_path"), command)
            ),
            needs="simtrace2-remsim running for the board (Plexus never starts it)",
        )

    return SimTraceDiscovery(boards=boards)


def open_virtual_card(
    os_: Any, attack: Any, host: Optional[str] = None, port: Optional[int] = None
) -> Any:
    """
    atrium's ``VirtualCard``: the vpicc that answers vpcd for a terminal.

    ``os_`` is asked for the ATR and told about power; ``attack.user_execute``
    answers each command APDU. That is the pair atrium's own relay hands it
    (a ``RelayOS`` and an ``InterceptAttack``), and the seam where a Plexus
    chain stands in for both.

    Connecting is where vpcd is first asked for, so this is where a missing
    one is refused. ``VirtualCard`` calls ``sys.exit()`` when it cannot
    connect, which on the relay thread would end the thread with no message;
    it is turned into one here. The address is read when called, not when this
    module loaded, so a test can point it at a stand-in vpcd.
    """
    host = host or VPCD_HOST
    port = port or VPCD_PORT
    require_atrium()
    try:
        from virtual_card import VirtualCard
    except ImportError as exc:  # pragma: no cover - only if atrium moves
        raise AtriumUnavailable(f"atrium's virtual card will not import: {exc}") from exc
    try:
        return VirtualCard(host, port, os_, attack)
    except SystemExit:
        raise AtriumUnavailable(
            "Could not reach vpcd at %s:%d, which is where simtrace2-remsim hands "
            "the terminal's commands over. Is pcscd running with vpcd loaded "
            "(the virtualsmartcard package)? `pcsc_scan` should list a "
            "'Virtual PCD' reader." % (host, port)
        ) from None


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

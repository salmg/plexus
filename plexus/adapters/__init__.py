"""
Where Plexus reaches into atrium.

Every import of atrium goes through this package and nowhere else.  Atrium's
modules import each other by top-level name (``from transport.base import
CardTransport``), so its checkout has to be on ``sys.path`` — a global side
effect, done once, in one place you can find.

Keeping the seam narrow is the point.  ``vendor/atrium`` is a pinned submodule
that is never edited, so when it moves, the things that break are in here.
"""

from plexus.adapters.atrium import (  # noqa: F401
    AtriumUnavailable,
    ReaderDiscovery,
    SimTraceDiscovery,
    load_mutation_engine,
    load_recorded_card,
    open_local_card,
    open_virtual_card,
    pcsc_discovery,
    pcsc_readers,
    require_atrium,
    simtrace_discovery,
)

__all__ = [
    "AtriumUnavailable",
    "ReaderDiscovery",
    "SimTraceDiscovery",
    "load_mutation_engine",
    "load_recorded_card",
    "open_local_card",
    "open_virtual_card",
    "pcsc_discovery",
    "pcsc_readers",
    "simtrace_discovery",
    "require_atrium",
]

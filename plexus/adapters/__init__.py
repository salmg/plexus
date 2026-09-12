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
    load_mutation_engine,
    load_recorded_card,
    require_atrium,
)

__all__ = [
    "AtriumUnavailable",
    "load_mutation_engine",
    "load_recorded_card",
    "require_atrium",
]

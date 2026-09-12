"""
What every suite here shares.

One thing, and it exists because of how a fresh clone behaves. `git clone`
without `--recurse-submodules` leaves `vendor/atrium` an empty directory, and
almost everything that *runs* a graph needs it: a capture node loads atrium's
recorded transport, a mutation node hands the exchange to atrium's engine. So
those tests cannot pass, and without this they did not fail quickly either —
the server suite waits on frames a runtime that cannot bind will never send,
and every one of those waits runs out its deadline first.

Bounded waits made that finite. Skipping makes it instant, and says what to do.
"""

from __future__ import annotations

import pytest


def atrium_available() -> bool:
    """Is the submodule checked out?"""
    from plexus.adapters import AtriumUnavailable, require_atrium

    try:
        require_atrium()
    except AtriumUnavailable:
        return False
    return True


#: For the modules that cannot do anything without it.
needs_atrium = pytest.mark.skipif(
    not atrium_available(),
    reason="needs the atrium submodule: git submodule update --init vendor/atrium",
)

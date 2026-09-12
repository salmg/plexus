"""
Plexus — a node canvas for atrium.

Wire readers, phones, captures and mutations into a relay you can see, run and
reach into while it runs.

This package is the runtime half: it reads a ``.plexus.yaml`` graph, checks it,
binds the hardware it names and moves APDUs along it.  The editor lives in
``ui/`` and talks to this over the WebSocket protocol in ``PROTOCOL.md``.

Nothing here imports atrium at module level.  The graph model and the catalogue
are pure, so they can be read and tested on a machine with no reader attached
and no pyscard installed; only the adapters under ``plexus.adapters`` reach for
``vendor/atrium``.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]

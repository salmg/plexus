"""
Where a graph is allowed to read and write.

A graph is untrusted input by design — it arrives over a socket or as a file
someone sent you, and it names files. Before this existed, a trace node's
``path`` was an arbitrary file write: anywhere the process could reach, one
appended JSON line at a time. That is a small primitive and a real one.

So every path a node takes is resolved inside a **workspace**, and anything
that escapes it is refused by name. The workspace is the directory the runtime
was started in, which makes the common case — a capture beside the graph that
reads it — work with no ceremony, and the dangerous case impossible.

Set ``PLEXUS_WORKSPACE`` to widen it deliberately. Widening it is a decision
someone makes once, at the shell, rather than something a graph can do to them.
"""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_VARIABLE = "PLEXUS_WORKSPACE"


class PathRefused(ValueError):
    """A node asked for a path outside the workspace. User-facing."""


def workspace() -> Path:
    """The directory graphs may reach inside. Resolved every call, so a test
    or a long-running server that changes it is not fighting a cached value."""
    configured = os.environ.get(WORKSPACE_VARIABLE)
    return Path(configured).resolve() if configured else Path.cwd().resolve()


def _inside(candidate: Path, root: Path) -> bool:
    """
    Is `candidate` at or below `root`?

    Compared on resolved paths so `..` and symlinks are already collapsed;
    string prefixes would accept `/srv/plexus-evil` for a root of
    `/srv/plexus`.
    """
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_read(raw: str, *, what: str = "file") -> Path:
    """A path a node may read. Must exist, and must be inside the workspace."""
    root = workspace()
    target = Path(raw).resolve() if Path(raw).is_absolute() else (root / raw).resolve()

    if not _inside(target, root):
        raise PathRefused(
            "%s %s is outside the workspace (%s). Move it in, or start the "
            "runtime with %s set to a directory that contains it."
            % (what.capitalize(), raw, root, WORKSPACE_VARIABLE)
        )
    if not target.is_file():
        raise PathRefused("No %s at %s." % (what, target))
    return target


def resolve_write(raw: str, *, what: str = "file") -> Path:
    """
    A path a node may write. Parent directories are created, inside only.

    The file need not exist — this is where a trace is about to go — but the
    directory it lands in is created under the workspace and nowhere else.
    """
    root = workspace()
    target = Path(raw).resolve() if Path(raw).is_absolute() else (root / raw).resolve()

    if not _inside(target, root):
        raise PathRefused(
            "Refusing to write %s outside the workspace (%s). Choose a path "
            "inside it, or start the runtime with %s set."
            % (raw, root, WORKSPACE_VARIABLE)
        )
    if target.is_dir():
        raise PathRefused("%s is a directory." % target)

    target.parent.mkdir(parents=True, exist_ok=True)
    return target

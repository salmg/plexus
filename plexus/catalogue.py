"""
The node catalogue, loaded rather than written.

``plexus/schema/catalogue.json`` is generated from ``ui/src/catalogue/index.ts`` by
``npm run export:catalogue``.  There is exactly one place a node type is
described, and both the editor and the runtime read it — a Python copy of the
same table would drift within a week, and the first symptom would be a graph
that draws in the editor and refuses to run, or worse, runs differently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

#: The catalogue ships *inside* the package, not beside it.
#
# It used to sit at the repository root, which works for every way this project
# is actually developed — an editable install, or a plain clone — and fails for
# the one way it reaches someone else. `pip install .` collects `plexus*` and
# nothing above it, so the installed tree had no `schema/` beside the package
# at all: a non-editable install produced a `plexus` command that could not
# answer a single subcommand. Measured, not assumed.
#
# The file's only reader is Python — the editor authors the TypeScript the
# generator reads — so the package is where it belongs.
CATALOGUE_PATH = Path(__file__).resolve().parent / "schema" / "catalogue.json"


class CatalogueError(RuntimeError):
    """The catalogue is missing or unreadable."""


@dataclass(frozen=True)
class Port:
    id: str
    channel: str
    role: str
    label: str
    oob: bool = False


@dataclass(frozen=True)
class Field:
    key: str
    kind: str
    label: str
    default: Any = None
    help: str | None = None
    device_kind: str | None = None
    options: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class NodeType:
    key: str
    title: str
    node_class: str
    summary: str
    ports: tuple[Port, ...]
    fields: tuple[Field, ...]
    latency_ms: int
    device: str | None = None
    caveats: tuple[str, ...] = ()


class Catalogue:
    """Lazily loaded so importing this module never touches the disk."""

    def __init__(self, path: Path = CATALOGUE_PATH) -> None:
        self._path = path

    @cached_property
    def types(self) -> dict[str, NodeType]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CatalogueError(
                f"No catalogue at {self._path}. "
                "Run `npm run export:catalogue` in ui/ to generate it."
            ) from exc
        except json.JSONDecodeError as exc:
            raise CatalogueError(
                f"Catalogue at {self._path} is not valid JSON: {exc}"
            ) from exc

        return {entry["key"]: _node_type(entry) for entry in raw.get("nodeTypes", [])}

    def get(self, key: str) -> NodeType | None:
        return self.types.get(key)

    def require(self, key: str) -> NodeType:
        found = self.types.get(key)
        if found is None:
            raise KeyError(f"Unknown node type: {key}")
        return found

    def __iter__(self):
        return iter(self.types.values())

    def __len__(self) -> int:
        # Nothing calls this today. It stays because `__iter__` is here:
        # a type that can be iterated and cannot be measured is a container
        # that behaves like one only half the time.
        return len(self.types)


def _node_type(entry: dict[str, Any]) -> NodeType:
    return NodeType(
        key=entry["key"],
        title=entry["title"],
        node_class=entry["nodeClass"],
        summary=entry.get("summary", ""),
        ports=tuple(
            Port(
                id=p["id"],
                channel=p["channel"],
                role=p["role"],
                label=p.get("label", p["id"]),
                oob=bool(p.get("oob", False)),
            )
            for p in entry.get("ports", [])
        ),
        fields=tuple(
            Field(
                key=f["key"],
                kind=f["kind"],
                label=f.get("label", f["key"]),
                default=f.get("default"),
                help=f.get("help"),
                device_kind=f.get("deviceKind"),
                options=tuple(f.get("options", ())),
            )
            for f in entry.get("fields", [])
        ),
        latency_ms=int(entry.get("latencyMs", 0)),
        device=entry.get("device"),
        caveats=tuple(entry.get("caveats", ())),
    )


CATALOGUE = Catalogue()

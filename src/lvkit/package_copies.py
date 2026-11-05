"""The files a generated package holds as copies of files beside the VIs.

A Call Library Function Node's library, named relative to its VI, is copied into
the package beside the VI's module (``ModuleLayout.placed_beside``). Each place
in the package holds one file: the same file named by two VIs is copied once,
and two different files that would land in one place (two copies of a VI placed
by one recorded path, or two folders whose names transform to one package) stop
generation, naming both VIs and both files, as two files that generate one
module do. The copies are written once every module has been generated.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.module_layout import ModuleCollisionError, ModuleLayout


class CopyCollisionError(ModuleCollisionError):
    """Two different files that would be copied to one place in the package."""


@dataclass(frozen=True)
class _Copy:
    """A file to copy and the VI that names it."""

    source: Path
    vi_key: str


class PackageCopies:
    """The files copied into the package under ``output_dir``, by their place in
    it (the parts below ``output_dir``)."""

    def __init__(
        self, graph: InMemoryVIGraph, layout: ModuleLayout, output_dir: Path
    ) -> None:
        self._graph = graph
        self._layout = layout
        self._output_dir = output_dir
        self._copies: dict[tuple[str, ...], _Copy] = {}
        self._clashes: list[str] = []

    def add(
        self,
        vi_key: str,
        recorded: Sequence[str],
        below: Sequence[str],
        source: Path,
    ) -> Path | None:
        """Copy ``source``, at ``below`` in the folder of the file the VI
        ``vi_key`` records relative to itself (``recorded``), to the same place
        from the VI's module (``ModuleLayout.placed_beside``); the path it is
        copied to, None when that place leaves the VI's tree. A different file
        already copied there is a clash (``check``)."""
        placed = self._layout.placed_beside(vi_key, recorded)
        if placed is None:
            return None
        parts = (*placed, *below)
        copy = _Copy(source.resolve(), vi_key)
        held = self._copies.setdefault(parts, copy)
        if held.source != copy.source:
            self._clashes.append(self._clash(parts, held, copy))
        return self._output_dir.joinpath(*parts)

    def _clash(self, parts: tuple[str, ...], held: _Copy, copy: _Copy) -> str:
        named = [
            f"{self._graph.vi_label(c.source)!r} (named by "
            f"{self._graph.key_label(c.vi_key)!r})"
            for c in (held, copy)
        ]
        return (
            f"{named[0]} and {named[1]} are both copied to "
            f"{'/'.join(parts)!r} in the package."
        )

    def check(self) -> None:
        """Stop generation when two different files would be copied to one
        place, naming the first such pair."""
        if self._clashes:
            raise CopyCollisionError(self._clashes[0])

    def write(self) -> None:
        """Copy every file to its place in the package."""
        for parts, copy in sorted(self._copies.items()):
            target = self._output_dir.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(copy.source, target)

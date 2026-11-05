"""The files a generated package holds as copies of files beside the VIs.

A Call Library Function Node's library, named relative to its VI, is copied into
the package beside the VI's module (``ModuleLayout.placed_beside``). The module
being generated registers its files; they are kept only when that module is
written (``commit``), never for a module that fails. Each place in the package
holds one file, a place compared without regard to case (a Windows or macOS file
system names one file whatever the case): the same file named twice is copied
once, and two different files for one place (two copies of a VI placed by one
recorded path, two folders whose names transform to one package, two names that
differ only in case) are a clash that stops generation, naming both VIs and both
files, as two files that generate one module do. The kept files are written once
every module has been generated, before the clash is raised, so the package
written is whole.
"""

from __future__ import annotations

import os
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
    """A file to copy, the VI that names it, and its place in the package as
    named (its parts below the package folder)."""

    source: Path
    vi_key: str
    parts: tuple[str, ...]


class PackageCopies:
    """The files copied into the package under ``output_dir``."""

    def __init__(
        self, graph: InMemoryVIGraph, layout: ModuleLayout, output_dir: Path
    ) -> None:
        self._graph = graph
        self._layout = layout
        self._output_dir = output_dir
        # Kept copies by their place compared without case.
        self._copies: dict[tuple[str, ...], _Copy] = {}
        # The copies of the module being generated, until it is written.
        self._pending: list[_Copy] = []
        self._clashes: list[str] = []

    def register(
        self,
        vi_key: str,
        recorded: Sequence[str],
        below: Sequence[str],
        source: Path,
    ) -> None:
        """Register ``source``, at ``below`` in the folder of the file the VI
        ``vi_key`` records relative to itself (``recorded``), to be copied to the
        same place from the VI's module (``ModuleLayout.placed_beside``) once
        the module being generated is written. A place that leaves the VI's
        tree registers nothing."""
        parts = self._layout.placed_beside(vi_key, recorded, below)
        if parts is not None:
            self._pending.append(_Copy(source.resolve(), vi_key, parts))

    def begin_module(self) -> None:
        """Forget what the last module registered when it was not written."""
        self._pending.clear()

    def commit(self) -> None:
        """Keep what the module just written registered; a different file
        already kept at one of its places is a clash (``check``)."""
        for copy in self._pending:
            held = self._copies.setdefault(_folded(copy.parts), copy)
            if held.source != copy.source:
                self._clashes.append(self._clash(held, copy))
        self._pending.clear()

    def _clash(self, held: _Copy, copy: _Copy) -> str:
        first, second = _distinct(held.source, copy.source)
        return (
            f"{first!r} (named by {self._graph.key_label(held.vi_key)!r}) and "
            f"{second!r} (named by {self._graph.key_label(copy.vi_key)!r}) are "
            f"both copied to {'/'.join(copy.parts)!r} in the package."
        )

    def write(self) -> None:
        """Copy every kept file to its place in the package (the first one kept
        where there is a clash)."""
        for copy in sorted(self._copies.values(), key=lambda c: c.parts):
            target = self._output_dir.joinpath(*copy.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(copy.source, target)

    def check(self) -> None:
        """Stop generation when two different files were kept for one place,
        naming the first such pair."""
        if self._clashes:
            raise CopyCollisionError(self._clashes[0])


def _folded(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.casefold() for part in parts)


def _distinct(first: Path, second: Path) -> tuple[str, str]:
    """The two files as a message tells them apart: each one's folders and name
    below the folder they share (``.../copy1/A/x.dll``), never that folder or
    the ones above it, so no absolute path or user name is shown."""
    shared = Path(os.path.commonpath([first, second]))
    return (
        ".../" + first.relative_to(shared).as_posix(),
        ".../" + second.relative_to(shared).as_posix(),
    )

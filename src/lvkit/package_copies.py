"""The files a generated package holds as copies of files beside the VIs.

A Call Library Function Node's library, named relative to its VI, is copied into
the package beside the VI's module (``ModuleLayout.placed_beside``). Each module
being generated registers its files on its own ``ModuleCopies``
(``PackageCopies.module``); they are kept only when that module is written
(``ModuleCopies.commit``), so a module that fails copies nothing. Each place in
the package holds one file, a place compared without regard to case (a Windows or
macOS file system names one file whatever the case): the same file named twice
is kept once, and two different files for one place (two copies of a VI placed by
one recorded path, two folders whose names transform to one package, two names
that differ only in case) are a clash, naming both VIs and both files.

The kept files are written once, when generation ends, whether it ran to the end
or a fail-fast run stopped (``write``), so every module written finds the files
it loads; then a clash stops generation (``check``), the package written whole.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.module_layout import ModuleCollisionError, ModuleLayout, ordinals


class CopyCollisionError(ModuleCollisionError):
    """Two different files that would be copied to one place in the package."""


@dataclass(frozen=True)
class _Copy:
    """A file to copy, the VI that names it, and its place in the package as
    named (its parts below the package folder)."""

    source: Path
    vi_key: str
    parts: tuple[str, ...]


class ModuleCopies:
    """The files one module being generated names beside its VIs, kept by the
    package only when the module is written (``commit``)."""

    def __init__(self, package: PackageCopies) -> None:
        self._package = package
        self._registered: list[_Copy] = []

    def register(
        self,
        vi_key: str,
        recorded: Sequence[str],
        below: Sequence[str],
        source: Path,
    ) -> None:
        """Register ``source``, at ``below`` in the folder of the file the VI
        ``vi_key`` records relative to itself (``recorded``), to be copied to the
        same place from the VI's module (``ModuleLayout.placed_beside``). A place
        that leaves the VI's tree registers nothing."""
        parts = self._package.layout.placed_beside(vi_key, recorded, below)
        if parts is not None:
            self._registered.append(_Copy(source.resolve(), vi_key, parts))

    def commit(self) -> None:
        """The module was written: keep what it registered."""
        self._package.keep(self._registered)
        self._registered = []


class PackageCopies:
    """The files copied into the package under ``output_dir``."""

    def __init__(
        self, graph: InMemoryVIGraph, layout: ModuleLayout, output_dir: Path
    ) -> None:
        self._graph = graph
        self.layout = layout
        self._output_dir = output_dir
        # Kept copies by their place compared without case.
        self._copies: dict[tuple[str, ...], _Copy] = {}
        self._clashes: list[str] = []

    def module(self) -> ModuleCopies:
        """The files of one module being generated."""
        return ModuleCopies(self)

    def keep(self, copies: Sequence[_Copy]) -> None:
        """Keep ``copies``, of a module just written; a different file already
        kept at one of their places is a clash (``check``)."""
        for copy in copies:
            held = self._copies.setdefault(_folded(copy.parts), copy)
            if held.source != copy.source:
                self._clashes.append(self._clash(held, copy))

    def _clash(self, held: _Copy, copy: _Copy) -> str:
        """Both files by the label every view gives a file (``vi_label``:
        project-relative, or ``<vilib>/...``, never an absolute path), numbered
        in the order they were kept when the labels are the same."""
        labels = (self._graph.vi_label(held.source), self._graph.vi_label(copy.source))
        numbers = ordinals(*labels, "copy")
        said = [
            f"{label!r} ({f'{number}, ' if number else ''}named by "
            f"{self._graph.key_label(c.vi_key)!r})"
            for label, number, c in zip(
                labels, numbers or (None, None), (held, copy), strict=True
            )
        ]
        return (
            f"{said[0]} and {said[1]} are both copied to "
            f"{'/'.join(copy.parts)!r} in the package."
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

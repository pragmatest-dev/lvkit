"""A dependency's path as its caller stores it, and what was found for it.

``dependency_path`` answers, for one dependency of one VI: the path the VI stores
for it (``stored_path``), whether the file is missing, and where the VI search
path found it when that is not where the stored path points.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lvkit.graph.errors import AmbiguousVIReferenceError
from lvkit.stored_path import to_stored

if TYPE_CHECKING:
    from lvkit.graph.core import InMemoryVIGraph
    from lvkit.parser.models import ParsedDependencyRef


@dataclass(frozen=True)
class DependencyPath:
    """``stored``: the path the caller stores, None only when it records none.
    ``missing``: the dependency's file was not found. ``found``: the file's path
    from the calling VI's folder, set only when the file was found somewhere other
    than where ``stored`` points."""

    stored: str | None
    missing: bool
    found: str | None = None


def dependency_path(graph: InMemoryVIGraph, key: str, caller: str) -> DependencyPath:
    """The path VI ``caller`` stores for its dependency ``key``. A dependency the
    caller recorded no path for (a class member reached through its class) is
    written by the installation path its callers recorded, if any."""
    ref = graph.recorded_ref(key, caller)
    if ref is not None:
        stored: str | None = to_stored(ref.path_tokens, ref.path_type)
    else:
        installed = graph.recorded_path(key)
        stored = to_stored(installed.split("/"), None) if installed else None
    missing = graph.is_stub(key)
    found = None if missing or ref is None else _found(graph, key, caller, ref)
    return DependencyPath(stored, missing, found)


def _found(
    graph: InMemoryVIGraph, key: str, caller: str, ref: ParsedDependencyRef
) -> str | None:
    """The file's path from ``caller``'s folder when it is not at any place
    ``ref`` points to on this machine (it was found on the VI search path)."""
    caller_file = graph.get_vi_source_path(caller)
    file = graph._dependency_file_path(key)  # noqa: SLF001
    if caller_file is None or file is None or not file.is_file():
        return None
    resolved = file.resolve()
    try:
        for candidate in graph.recorded_candidates(ref, caller_file):
            at = graph._locate_recorded(candidate, ref.name)  # noqa: SLF001
            if at is not None and at.resolve() == resolved:
                return None
    except AmbiguousVIReferenceError:
        return None
    try:
        shown = Path(os.path.relpath(resolved, caller_file.resolve().parent))
    except ValueError:
        return str(resolved)
    text = shown.as_posix()
    return text if text.startswith("..") else f"./{text}"

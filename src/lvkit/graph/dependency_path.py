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

from lvkit.stored_path import to_stored

if TYPE_CHECKING:
    from lvkit.graph.core import InMemoryVIGraph


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
    found = None if missing or ref is None else _found(graph, key, caller)
    return DependencyPath(stored, missing, found)


def _found(graph: InMemoryVIGraph, key: str, caller: str) -> str | None:
    """The file's path from ``caller``'s folder when it was found on the VI search
    path, not where the record points."""
    resolved = graph.found_elsewhere(key, caller)
    caller_file = graph.get_vi_source_path(caller)
    if resolved is None or caller_file is None:
        return None
    try:
        shown = Path(os.path.relpath(resolved, caller_file.resolve().parent))
    except ValueError:
        return str(resolved)
    text = shown.as_posix()
    return text if text.startswith("..") else f"./{text}"

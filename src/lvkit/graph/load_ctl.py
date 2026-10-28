"""Load ONE ``.ctl`` typedef into a fresh graph -- the single-control
counterpart of ``load_vi_by_path``."""

from __future__ import annotations

from pathlib import Path

from ..run_report import UnreadableVIError
from .core import InMemoryVIGraph


def load_ctl_by_path(
    path: Path | str,
    *,
    search_paths: list[Path] | None = None,
    graph: InMemoryVIGraph | None = None,
) -> tuple[InMemoryVIGraph, str]:
    """Load the ``.ctl`` at ``path`` into ``graph`` (a fresh one when None) and return
    ``(graph, ctl_key)`` -- the control's path key. Raises ``FileNotFoundError``
    when the file is absent and ``UnreadableVIError`` (with what was recorded on
    the control's node) when it can't be read as a control."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Control not found: {path}")
    if graph is None:
        graph = InMemoryVIGraph()
    key = graph.load_typedef(path, search_paths=search_paths)
    if graph.is_stub(key):
        recorded = graph.problems([key])
        reason = recorded[0].reason if recorded else "could not be read as a control"
        detail = recorded[0].detail if recorded else ()
        raise UnreadableVIError(reason, detail, path)
    return graph, key

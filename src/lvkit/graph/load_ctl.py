"""Load ONE ``.ctl`` typedef into a fresh graph -- the single-control
counterpart of ``load_vi_by_path``."""

from __future__ import annotations

from pathlib import Path

from .core import InMemoryVIGraph


def load_ctl_by_path(
    path: Path | str, *, search_paths: list[Path] | None = None
) -> tuple[InMemoryVIGraph, str]:
    """Load the ``.ctl`` at ``path`` into a fresh graph and return
    ``(graph, ctl_key)`` -- the control's path key. Raises ``FileNotFoundError``
    when the file is absent and ``ValueError`` when it can't be read as a
    control."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Control not found: {path}")
    graph = InMemoryVIGraph()
    key = graph.load_typedef(path, search_paths=search_paths)
    if graph.is_stub(key):
        raise ValueError(f"Could not read {path.name} as a control")
    return graph, key

"""Project-store discovery + primitive/vilib resolver configuration."""

from __future__ import annotations

from pathlib import Path

from ... import primitive_resolver, vilib_resolver
from ...project_store import find_project_store


def _configure_resolvers_for_vi(vi_path: str | Path) -> None:
    """Discover ``.lvkit/`` from a path and reset the primitive/vilib resolvers.

    One MCP session may serve several projects, so the project store is
    re-resolved on every call that knows a target path. The path may be a file
    (a ``.vi``), a directory, or not exist yet — we start from the path itself
    when it is a directory, its parent otherwise, then walk up for ``.lvkit/``.
    """
    p = Path(vi_path).resolve()
    start = p if p.is_dir() else p.parent
    store = find_project_store(start=start)
    primitive_resolver.reset_resolver(project_data_dir=store)
    vilib_resolver.reset_resolver(project_data_dir=store)


__all__ = ["_configure_resolvers_for_vi"]

"""Project-store discovery for MCP tool calls."""

from __future__ import annotations

from pathlib import Path

from ...project_store import find_project_store


def project_store_for(path: str | Path) -> Path | None:
    """The ``.lvkit/`` folder of the project ``path`` is in, else ``None``.

    One MCP session may serve several projects, so every call that knows a target
    path finds its own project store; the catalogs of that store are built once
    and shared (``lvkit.catalogs``). The path may be a file (a ``.vi``), a
    directory, or not exist yet: the search starts at the path itself when it is a
    directory, its parent otherwise, then walks up for ``.lvkit/``.
    """
    p = Path(path).resolve()
    start = p if p.is_dir() else p.parent
    return find_project_store(start=start)


__all__ = ["project_store_for"]

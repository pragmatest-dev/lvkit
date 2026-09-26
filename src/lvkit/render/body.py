"""The one ``path -> output body`` render entry: a ``.ctl`` control renders its
front panel, anything else renders as a VI's block diagram. The output cache
(``output_cache.cached_render``) and every frontend go through this, so the
choice of view by file kind is made in exactly one place."""

from __future__ import annotations

from pathlib import Path

from ..load_mode import LoadMode
from . import ThemeMode, render_vi_body
from .ctl import render_ctl_body


def render_body(
    path: Path,
    *,
    fmt: str = "html",
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    mode: LoadMode = LoadMode.MINIMAL,
    theme_mode: ThemeMode = "auto",
    ref: str | None = None,
) -> str | None:
    """Render ``path`` to ``fmt`` (``"svg"`` or ``"html"``). ``vilib_root``,
    ``userlib_root`` and ``mode`` shape a VI's dependency load; a control has
    no dependencies to resolve, so they don't apply to it."""
    if path.suffix.lower() == ".ctl":
        return render_ctl_body(
            path, fmt=fmt, search_paths=search_paths, theme_mode=theme_mode, ref=ref
        )
    return render_vi_body(
        path,
        fmt=fmt,
        search_paths=search_paths,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
        mode=mode,
        theme_mode=theme_mode,
        ref=ref,
    )

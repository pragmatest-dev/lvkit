"""Render a ``.ctl`` control's front panel: the ``.ctl`` counterpart of
``render_vi_body`` (``render/__init__.py``). Loads the control into a graph and
projects its own front panel -- there is no block diagram."""

from __future__ import annotations

from pathlib import Path

from ..graph.load_ctl import load_ctl_by_path
from . import ThemeMode
from .front_panel import render_ctl_front_panel
from .render_viewer import build_ctl_viewer


def render_ctl_body(
    path: Path,
    *,
    fmt: str = "html",
    search_paths: list[Path] | None = None,
    theme_mode: ThemeMode = "auto",
    ref: str | None = None,
) -> str | None:
    """The ``path -> output body`` render of one ``.ctl``: the raw SVG for
    ``fmt="svg"``, otherwise the self-contained zoom/pan viewer page. ``ref`` (a
    git rev) is appended to the viewer title. Raises ``FileNotFoundError`` /
    ``ValueError`` when the control can't be read; None when it has no front
    panel to draw."""
    graph, key = load_ctl_by_path(path, search_paths=search_paths)
    svg = render_ctl_front_panel(graph, key, theme_mode=theme_mode)
    if svg is None or fmt != "html":
        return svg
    title = graph.get_typedef(key).name
    if ref:
        title = f"{title} ({ref})"
    return build_ctl_viewer(svg, title=title)

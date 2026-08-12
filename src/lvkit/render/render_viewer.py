"""Build the single-VI render viewer HTML page.

A PURE builder mirroring :mod:`lvkit.render.diff_viewer`: given ONE already-
rendered SVG, staples it into a self-contained HTML page with a thin toolbar
(title + zoom group + the shared diagram-theme control) and a single scrollable,
zoom/pan pane. No VI loading, no disk reads beyond the packaged template, no
argv — the caller (the CLI's ``render --format html``, a future VSCode
extension) renders the SVG and passes it in.

The embedded SVG is expected to be rendered ``--theme auto`` (so the toolbar's
theme button can re-theme it live via ``document.documentElement``'s
``data-theme``) and interactive (it keeps its own frame-toggle/hover JS + root
id; this viewer only rescales + pans it).
"""

from __future__ import annotations

from importlib.resources import files

from .help_tip import HELP_TIP
from .properties_panel import PROPERTIES_BUTTON, PROPERTIES_PANEL
from .theme_control import THEME_CONTROL_BUTTON, THEME_CONTROL_SCRIPT

__all__ = ["build_render_viewer"]


def build_render_viewer(svg: str, *, title: str) -> str:
    """Render the single-pane render viewer page for one VI.

    ``svg`` is the inline block-diagram SVG (rendered ``theme_mode="auto"``).
    ``title`` labels the toolbar. Returns the full HTML document as a string,
    prefixed with a doctype + charset meta tag so it's a valid standalone file
    AND so an extension's charset-anchored CSP injection matches (same prefix
    contract as :func:`lvkit.render.diff_viewer.build_diff_viewer`).
    """
    template = (
        files("lvkit.render") / "templates" / "render_viewer.html"
    ).read_text(encoding="utf-8")

    html = (
        template.replace("__TITLE__", title)
        .replace("__THEME_BTN__", THEME_CONTROL_BUTTON)
        .replace("__THEME_SCRIPT__", THEME_CONTROL_SCRIPT)
        .replace("__PROPERTIES_BTN__", PROPERTIES_BUTTON)
        .replace("__PROPERTIES_PANEL__", PROPERTIES_PANEL)
        .replace("__HELP_TIP__", HELP_TIP)
        .replace("__SVG__", svg)
    )
    return "<!doctype html>\n<meta charset='utf-8'>\n" + html

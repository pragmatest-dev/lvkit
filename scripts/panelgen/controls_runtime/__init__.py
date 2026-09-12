"""Runtime controls for LabVIEW front-panel types, composed from NATIVE NiceGUI
elements (ui.column/ui.row/ui.input/ui.number/ui.button) — no custom Vue.

Copied verbatim (as a package, ``shutil.copytree``) into each generated panel
dir as ``controls/`` so a generated panel stays self-contained (the gallery
loads panels standalone). ``panel.py`` does ``from controls import ...`` for
whatever this VI's panel needs — this module is the public surface; the
cohesive submodules (``theme``/``filepicker``/``arraygrid``/``runtoolbar``/
``waveform``/``_util``) are its implementation.
"""

from __future__ import annotations

from nicegui import ui

from .arraygrid import ArrayField, array_control
from .filepicker import path_control
from .runtoolbar import RunController, toolbar
from .theme import CAPTION_CLS, CLASSIC, MODERN, Theme, set_theme, theme
from .waveform import waveform_indicator

__all__ = [
    "ArrayField",
    "array_control",
    "path_control",
    "waveform_indicator",
    "RunController",
    "toolbar",
    "set_theme",
    "theme",
    "Theme",
    "MODERN",
    "CLASSIC",
    "CAPTION_CLS",
]

# The ✕ delete affordance is hidden until its row is hovered (uncluttered), grey
# then, red when you point at it. Registered at PACKAGE IMPORT (before ui.run) so
# it lands in the real <head> -- calling ui.add_css/ui.html from inside a page
# build runs after the head is sent, so the rule never applies.
ui.add_css(
    # ✕ delete: hidden until the row is hovered.
    ".lv-del{color:#9ca3af;opacity:0;transition:opacity .12s}"
    ".ag-row-hover .lv-del{opacity:.85}"
    ".lv-del:hover{color:#ef4444;opacity:1 !important}"
    # Drag handle: hidden (takes no space) until the row is hovered, and small
    # when shown -- so it never crowds the index digits.
    ".ag-drag-handle{display:none !important}"
    ".ag-row-hover .ag-drag-handle{display:inline-block !important;"
    "opacity:.5;transform:scale(.8)}"
    # Path-cell Browse glyph: centered folder icon, brighter on hover.
    ".lv-browse{display:flex;align-items:center;justify-content:center;"
    "opacity:.6;transition:opacity .12s}"
    ".ag-row-hover .lv-browse{opacity:.8}"
    ".lv-browse:hover{opacity:1}",
    shared=True,  # apply to every page (default shared=False needs a live client)
)

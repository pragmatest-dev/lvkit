"""Viewer chrome that PROMOTES the VI's icon + connector-pane aside.

The rendered SVG embeds the VI's icon raster and its connector-pane face as a
``display:none`` group pinned top-right (``render/__init__.py``'s
``_vi_aside_svg``) — hidden in the standalone file so it never clutters the
diagram or leaks into a rasterization. This module is the render/diff viewers'
toggle: one toolbar button that reveals that aside IN PLACE (clears its inline
``display:none``), overlaid on the diagram's top-right like a real VI's corner.
Because it toggles by class selector, the diff viewer's ONE button reveals BOTH
panes' asides at once — the whole point of showing them for a side-by-side
compare.

Same shared-placeholder-injection pattern as ``theme_control``/``help_tip``/
``properties_panel``: static HTML/JS blobs stapled into the templates via
``__CONNECTOR_PANE_BTN__``/``__CONNECTOR_PANE_SCRIPT__``.

CLEAN-ROOM + THEME: the button is plain UI — a unicode glyph + CSS, never an
NI-derived icon; it follows the viewer/system theme like every other toolbar
button and never draws into the SVG.
"""

from __future__ import annotations

__all__ = [
    "CONNECTOR_PANE_BUTTON",
    "CONNECTOR_PANE_GLYPH",
    "CONNECTOR_PANE_PANEL",
    "CONNECTOR_PANE_PANEL_BTN_ID",
    "CONNECTOR_PANE_SCRIPT",
]

CONNECTOR_PANE_PANEL_BTN_ID = "lvkitPaneBtn"

# U+25A6 "SQUARE WITH ORTHOGONAL CROSSHATCH FILL" — a plain grid glyph standing
# in for the connector-pane grid. A unicode character styled like every other
# toolbar button (theme "◐", zoom "−"/"+"/"⛶", properties "▤"), NOT a drawn icon.
CONNECTOR_PANE_GLYPH = "▦"

CONNECTOR_PANE_BUTTON = (
    f'<button id="{CONNECTOR_PANE_PANEL_BTN_ID}" type="button" '
    'class="lvkit-theme-btn" '
    'title="Show/hide the VI icon + connector pane" '
    'aria-label="Toggle VI icon and connector pane" '
    f'aria-expanded="false">{CONNECTOR_PANE_GLYPH}</button>'
)

# The fixed overlay the panel is promoted INTO — pinned to the viewer's own
# top-right corner (like a real VI's Context Help window), so it floats over
# the viewport, NOT the diagram's coordinate space (no diagram canvas around
# it, no scaling with zoom). Chrome-only tokens (defined on :root by the
# templates); never data-theme. Injected once near end-of-body.
CONNECTOR_PANE_PANEL = (
    "<style>\n"
    "  .lvkit-pane-overlay{position:fixed;top:56px;right:16px;z-index:75;\n"
    "    display:flex;flex-direction:column;gap:8px;max-width:calc(100% - 32px);\n"
    "    max-height:82vh;overflow:auto;\n"
    "    filter:drop-shadow(0 3px 14px rgba(0,0,0,.30))}\n"
    "  .lvkit-pane-overlay[hidden]{display:none}\n"
    "  .lvkit-pane-overlay svg{display:block;max-width:100%;height:auto}\n"
    "</style>\n"
    '<div class="lvkit-pane-overlay" id="lvkitPaneOverlay" hidden></div>'
)

# Promote by CLONING each aside's panel <svg> OUT of its <defs> into the fixed
# overlay above; hide by clearing it. The source stays in <defs> (structurally
# non-rendered), so nothing leaks when JS is stripped or the SVG is sanitized —
# only this script makes it visible. The diff viewer's two panes each contribute
# their panel (stacked). Robust to zero asides; never throws (optional chrome).
CONNECTOR_PANE_SCRIPT = (
    "<script>\n"
    "(function(){\n"
    "  try {\n"
    f'    var btn = document.getElementById("{CONNECTOR_PANE_PANEL_BTN_ID}");\n'
    '    var overlay = document.getElementById("lvkitPaneOverlay");\n'
    "    if (!btn || !overlay) return;\n"
    '    var panels = document.querySelectorAll("svg defs > .lv-vi-aside > svg");\n'
    "    if (!panels.length) { btn.disabled = true; return; }\n"
    "    btn.addEventListener('click', function () {\n"
    "      if (!overlay.hidden) {\n"
    "        overlay.hidden = true;\n"
    "        overlay.textContent = '';\n"
    "        btn.setAttribute('aria-expanded', 'false');\n"
    "        return;\n"
    "      }\n"
    "      overlay.textContent = '';\n"
    "      panels.forEach(function (p) {\n"
    "        overlay.appendChild(p.cloneNode(true));\n"
    "      });\n"
    "      overlay.hidden = false;\n"
    "      btn.setAttribute('aria-expanded', 'true');\n"
    "    });\n"
    "  } catch (e) { /* pane chrome is optional -- never break the viewer */ }\n"
    "})();\n"
    "</script>"
)

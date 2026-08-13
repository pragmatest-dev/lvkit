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
    "CONNECTOR_PANE_PANEL_BTN_ID",
    "CONNECTOR_PANE_SCRIPT",
    "DIFF_CONNECTOR_PANE_BUTTON",
    "DIFF_CONNECTOR_PANE_PANEL_BTN_ID",
    "DIFF_CONNECTOR_PANE_SCRIPT",
]

CONNECTOR_PANE_PANEL_BTN_ID = "lvkitPaneBtn"
DIFF_CONNECTOR_PANE_PANEL_BTN_ID = "lvkitDiffPaneBtn"

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

# Reveal by CLONING each aside OUT of its <defs> into the visible tree; hide by
# removing the clones. The source stays in <defs> (structurally non-rendered),
# so nothing leaks when JS is stripped or the SVG is sanitized — only this
# script, running in the viewer, ever makes it visible. Each clone is appended
# to its OWN owning <svg> (so the diff viewer's two panes each get theirs), as
# the last child so it paints on top, at the source group's transform (the VI's
# top-right corner). Robust to zero asides; never throws (optional chrome).
CONNECTOR_PANE_SCRIPT = (
    "<script>\n"
    "(function(){\n"
    "  try {\n"
    f'    var btn = document.getElementById("{CONNECTOR_PANE_PANEL_BTN_ID}");\n'
    "    if (!btn) return;\n"
    '    var defs = Array.prototype.slice.call(\n'
    '      document.querySelectorAll("svg defs > .lv-vi-aside")\n'
    '    ).filter(function (d) { return !d.closest(".mm-mirror"); });\n'
    "    if (!defs.length) { btn.disabled = true; return; }\n"
    "    var clones = [];\n"
    "    btn.addEventListener('click', function () {\n"
    "      if (clones.length) {\n"
    "        clones.forEach(function (c) { c.remove(); });\n"
    "        clones = [];\n"
    "        btn.setAttribute('aria-expanded', 'false');\n"
    "        return;\n"
    "      }\n"
    "      defs.forEach(function (d) {\n"
    "        var svg = d.ownerSVGElement;\n"
    "        if (!svg) return;\n"
    "        var c = d.cloneNode(true);\n"
    "        svg.appendChild(c);\n"
    "        clones.push(c);\n"
    "      });\n"
    "      btn.setAttribute('aria-expanded', 'true');\n"
    "    });\n"
    "  } catch (e) { /* aside chrome is optional -- never break the viewer */ }\n"
    "})();\n"
    "</script>"
)


# ---------------------------------------------------------------------------
# Diff-viewer variant: ONE ▦ button reveals BOTH connector panes at once as
# fixed per-pane overlays -- the BEFORE VI's pane pinned in beforeWrap's top-
# right, the AFTER's in afterWrap's -- for a stable side-by-side compare (unlike
# the render viewer's in-SVG clone, these don't pan/zoom away, so the change
# list can reliably spotlight a terminal). Each overlay is the panel <svg> lifted
# out of its pane's <defs> aside into a positioned <div>. The reveal is
# diff-agnostic; the viewer (diff_viewer.html) supplies the diff behaviour via
# three window hooks it defines:
#   * lvkitAnnotateConnectorPanes(boxes) -- ring + number the changed terminals
#   * lvkitBlendConnectorPanes()         -- fade the two overlays with the slider
#   * (this script exposes) lvkitShowConnectorPanes()/lvkitConnectorBoxes() so a
#     change click can open the panes and reach the live overlay <div>s.
# ---------------------------------------------------------------------------
DIFF_CONNECTOR_PANE_BUTTON = (
    f'<button id="{DIFF_CONNECTOR_PANE_PANEL_BTN_ID}" type="button" '
    'class="lvkit-theme-btn" '
    'title="Show/hide both connector panes (changed terminals ringed)" '
    'aria-label="Toggle connector panes" '
    f'aria-expanded="false">{CONNECTOR_PANE_GLYPH}</button>'
)

DIFF_CONNECTOR_PANE_SCRIPT = (
    "<script>\n"
    "(function(){\n"
    "  try {\n"
    f'    var btn = document.getElementById("{DIFF_CONNECTOR_PANE_PANEL_BTN_ID}");\n'
    "    if (!btn) return;\n"
    "    // Each pane's OWN aside panel svg + the wrap it pins into. Scoped by id\n"
    "    // (never the minimap's .mm-over/.mm-under clones of these same svgs).\n"
    "    var srcs = [\n"
    "      { wrap: 'beforeWrap', pane: 'beforePane', side: 'before' },\n"
    "      { wrap: 'afterWrap',  pane: 'afterPane',  side: 'after' }\n"
    "    ].map(function (s) {\n"
    "      var wrap = document.getElementById(s.wrap);\n"
    "      var pane = document.getElementById(s.pane);\n"
    "      var svg = pane && pane.querySelector('svg');\n"
    "      var def = svg && svg.querySelector('defs > .lv-vi-aside');\n"
    "      var panel = def && def.querySelector('svg');\n"
    "      return (wrap && panel)\n"
    "        ? { wrap: wrap, side: s.side, panel: panel } : null;\n"
    "    }).filter(Boolean);\n"
    "    if (!srcs.length) { btn.disabled = true; return; }\n"
    "    var boxes = [];\n"
    "    function hide() {\n"
    "      boxes.forEach(function (b) { b.remove(); });\n"
    "      boxes = [];\n"
    "      btn.setAttribute('aria-expanded', 'false');\n"
    "    }\n"
    "    function show() {\n"
    "      // The pane and the properties popover share the top-right view space --\n"
    "      // keep them MUTUALLY EXCLUSIVE so neither clutters the other.\n"
    "      if (window.lvkitCloseProps) window.lvkitCloseProps();\n"
    "      srcs.forEach(function (s) {\n"
    "        var box = document.createElement('div');\n"
    "        box.className = 'lvkit-pane-overlay ' + s.side;\n"
    "        box.dataset.side = s.side;\n"
    "        box.appendChild(s.panel.cloneNode(true));\n"
    "        s.wrap.appendChild(box);\n"
    "        boxes.push(box);\n"
    "      });\n"
    "      btn.setAttribute('aria-expanded', 'true');\n"
    "      if (window.lvkitAnnotateConnectorPanes)\n"
    "        window.lvkitAnnotateConnectorPanes(boxes);\n"
    "      if (window.lvkitBlendConnectorPanes) window.lvkitBlendConnectorPanes();\n"
    "    }\n"
    "    btn.addEventListener('click', function () {\n"
    "      if (boxes.length) { hide(); } else { show(); }\n"
    "    });\n"
    "    // For the change list's click-to-view.\n"
    "    window.lvkitShowConnectorPanes = function () {\n"
    "      if (!boxes.length) { show(); }\n"
    "      return boxes;\n"
    "    };\n"
    "    window.lvkitConnectorBoxes = function () { return boxes; };\n"
    "    window.lvkitCloseConnectorPanes = function () { if (boxes.length) hide(); };\n"
    "  } catch (e) { /* aside chrome is optional -- never break the viewer */ }\n"
    "})();\n"
    "</script>"
)

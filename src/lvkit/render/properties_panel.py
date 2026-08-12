"""VI-properties chrome for the render viewer (task #19): a single-glyph
toolbar button + a collapsible "Properties" popover, driven ENTIRELY from the
root ``<svg>``'s ``data-lv-properties``/``data-lv-structure`` JSON attributes
(see ``render/__init__.py``'s ``_vi_properties_data_attrs``) — the SVG is the
single carrier, so the same data a host reading the raw SVG (e.g. the VS Code
extension) would parse is what this chrome reads too. The button + popover
are the SOLE properties surface — there is deliberately no always-visible
header summary (chips); header space is tight, and the popover is one click
away, so a second summary would only duplicate it.

Also supplies the DIFF viewer's equivalent (``DIFF_PROPERTIES_BUTTON``/
``DIFF_PROPERTIES_PANEL``): the SAME popover — grouped VALUES (Version/
Execution/Window/Toolbar/Instance/Structure), not a bespoke changes-only
list — but sourced from the AFTER pane's ``data-lv-*`` dataset (so it reads
like "the VI's current properties", exactly like the single-VI panel), with
any row whose value differs from the BEFORE pane's dataset HIGHLIGHTED amber.
"Like the single-VI property viewer, but with the modified items
highlighted." The row/group-building logic (``_PANEL_BODY_JS``) is ONE
shared JS block used verbatim by both panels — the single-VI panel simply
never supplies a "before" side, so ``changed`` is always false there and no
highlight class is ever applied (byte-identical rendered DOM to before this
module grew a diff variant).

CLEAN-ROOM + THEME rules (hard, per task #19):
  - Button/panel are PLAIN UI — text + CSS only. NEVER an NI-derived glyph (no
    LabVIEW broken-run-arrow, no drawn lock icon mimicking LabVIEW chrome).
    The button glyph is a plain unicode character (``PROPERTIES_GLYPH``),
    matching every other toolbar button's own style (theme "◐", zoom
    "−"/"+"/"⛶", highlight "◉") — not a drawn SVG icon.
  - They are viewer CHROME, so they follow the viewer/system theme via plain
    ``@media (prefers-color-scheme: dark)`` — same tokens the rest of
    ``render_viewer.html`` already defines (``--panel``/``--line``/``--fg``/
    ``--fg-muted``/``--btn``). They must NEVER react to ``data-theme`` (that
    toggle is diagram-only, see the ``render_viewer.html`` design-law
    comment) and never draw anything into the SVG itself. The diff-viewer
    variant leans on ``diff_viewer.html``'s own ``--mod`` token (the SAME
    amber the "modified" legend swatch/tag already use) for its changed-row/
    changed-ring colour, so a changed VI property reads with the same colour
    language as a changed node/wire.

Same shared-placeholder-injection pattern as :mod:`lvkit.render.theme_control`
and :mod:`lvkit.render.help_tip`: this module supplies static HTML/CSS/JS
blobs; ``render_viewer.py``/``diff_viewer.py`` staple them into their
templates via ``__PROPERTIES_BTN__``/``__PROPERTIES_PANEL__`` and
``__DIFF_PROPERTIES_BTN__``/``__DIFF_PROPERTIES_PANEL__`` respectively.
"""

from __future__ import annotations

__all__ = [
    "DIFF_PROPERTIES_BUTTON",
    "DIFF_PROPERTIES_PANEL",
    "DIFF_PROPERTIES_PANEL_BTN_ID",
    "PROPERTIES_BUTTON",
    "PROPERTIES_GLYPH",
    "PROPERTIES_PANEL",
    "PROPERTIES_PANEL_BTN_ID",
]

# The single element id each toggle button and its script agree on.
PROPERTIES_PANEL_BTN_ID = "lvkitPropsBtn"
DIFF_PROPERTIES_PANEL_BTN_ID = "lvkitDiffPropsBtn"

# U+25A4 "SQUARE WITH HORIZONTAL FILL" — a plain list glyph. ONE shared
# unicode glyph constant for the properties button in BOTH viewers, styled
# the same minimal way as every other toolbar button (theme "◐", zoom
# "−"/"+"/"⛶") via the shared ``.lvkit-theme-btn`` footprint class — NOT a
# drawn SVG icon.
PROPERTIES_GLYPH = "▤"

PROPERTIES_BUTTON = (
    f'<button id="{PROPERTIES_PANEL_BTN_ID}" type="button" class="lvkit-theme-btn" '
    'title="Show/hide VI properties" '
    'aria-label="Toggle VI properties panel" '
    f'aria-expanded="false">{PROPERTIES_GLYPH}</button>'
)

DIFF_PROPERTIES_BUTTON = (
    f'<button id="{DIFF_PROPERTIES_PANEL_BTN_ID}" type="button" '
    'class="lvkit-theme-btn" '
    'title="Show/hide VI properties (changed values highlighted)" '
    'aria-label="Toggle VI properties panel" '
    f'aria-expanded="false">{PROPERTIES_GLYPH}</button>'
)

# Shared panel-box CSS (position/size/typography) — ONE definition used by
# both PROPERTIES_PANEL and DIFF_PROPERTIES_PANEL via ``__TOP__`` (their
# headers sit at different heights). Chrome-only tokens (already defined on
# :root by both templates) — never data-theme (that toggle is diagram-only).
_PANEL_CSS = """
  .lvkit-props-panel{position:fixed;top:__TOP__px;right:16px;z-index:6;width:300px;
    max-width:calc(100% - 32px);max-height:70vh;overflow:auto;
    background:var(--panel);border:1px solid var(--line);border-radius:8px;
    box-shadow:0 2px 12px rgba(0,0,0,.28);padding:10px 12px;font-size:12px}
  .lvkit-props-panel[hidden]{display:none}
  .lvkit-props-panel h4{margin:10px 0 4px;font-size:11px;text-transform:uppercase;
    letter-spacing:.03em;color:var(--fg-muted)}
  .lvkit-props-panel h4:first-child{margin-top:0}
  .lvkit-props-panel dl{margin:0;display:grid;grid-template-columns:auto auto;
    justify-content:space-between;gap:2px 8px}
  .lvkit-props-panel dt{color:var(--fg-muted)}
  .lvkit-props-panel dd{margin:0;text-align:right;overflow-wrap:anywhere}
  .lvkit-props-empty{color:var(--fg-muted)}
"""

# Shared row/group/buildPanel JS — the ONE value-rendering implementation
# both panels use. Grouped like `describe` (Version / Execution / Window /
# Toolbar / Instance / Structure): EVERY field shows (unfiltered), like the
# diagram shows unchanged nodes -- "see every value, changed ones
# highlighted" -- not just the non-default ones (that filter used to hide
# most of a VI's actual properties from the single-VI popover).
#
# `beforeProps`/`beforeStruct` (declared by each panel's own preamble, see
# PROPERTIES_PANEL/DIFF_PROPERTIES_PANEL below) drive an OPTIONAL highlight:
# when set, a shown field whose value differs from the matching before-side
# value gets a `.lvkit-prop-changed` class. The single-VI panel's preamble
# always sets them to null, so `changed` is always false there and the class
# is never applied — this body is byte-identical shared code, not a
# diff-only fork.
_PANEL_BODY_JS = """
    var changedCount = 0;
    function row(dl, key, value, changed) {
      var dt = document.createElement("dt");
      dt.textContent = key;
      dt.dataset.key = key;
      var dd = document.createElement("dd");
      dd.textContent = String(value);
      dd.dataset.key = key;
      if (changed) {
        dt.className = "lvkit-prop-changed";
        dd.className = "lvkit-prop-changed";
        changedCount++;
      }
      dl.appendChild(dt);
      dl.appendChild(dd);
    }
    function group(title, obj, beforeObj) {
      if (!obj) return null;
      var keys = Object.keys(obj).sort();
      if (!keys.length) return null;
      var frag = document.createDocumentFragment();
      var h = document.createElement("h4");
      h.textContent = title;
      frag.appendChild(h);
      var dl = document.createElement("dl");
      keys.forEach(function (k) {
        var changed = beforeObj
          ? JSON.stringify(obj[k]) !== JSON.stringify(beforeObj[k]) : false;
        row(dl, k, obj[k], changed);
      });
      frag.appendChild(dl);
      return frag;
    }
    function buildPanel() {
      if (!panel) return;
      panel.innerHTML = "";
      var any = false;
      if (props) {
        var beforeTop = beforeProps ? {
          lock_state: beforeProps.lock_state, lv_version: beforeProps.lv_version,
          vi_type: beforeProps.vi_type,
        } : null;
        var top = group(
          "Version", { lock_state: props.lock_state, lv_version: props.lv_version,
                        vi_type: props.vi_type },
          beforeTop,
        );
        if (top) { panel.appendChild(top); any = true; }
        [
          ["Execution", props.execution, beforeProps ? beforeProps.execution : null],
          ["Window", props.window, beforeProps ? beforeProps.window : null],
          ["Toolbar", props.toolbar, beforeProps ? beforeProps.toolbar : null],
          ["Instance", props.instance, beforeProps ? beforeProps.instance : null],
        ].forEach(function (t) {
          var frag = group(t[0], t[1], t[2]);
          if (frag) { panel.appendChild(frag); any = true; }
        });
      }
      var structFrag = group("Structure", struct, beforeStruct);
      if (structFrag) { panel.appendChild(structFrag); any = true; }
      if (!any) {
        var p = document.createElement("div");
        p.className = "lvkit-props-empty";
        p.textContent = "(no properties available)";
        panel.appendChild(p);
      }
    }
    buildPanel();
"""

# Bundles: the panel CSS, the (initially empty/hidden) panel container, and
# the script that populates the panel from the embedded SVG's dataset and
# wires the toggle button. Injected once, near end-of-body, same spot as
# __HELP_TIP__. The properties button + this popover are the SOLE properties
# surface (no header status chips — dropped: with the popover always one
# click away, a second always-visible summary was redundant chrome).
PROPERTIES_PANEL = (
    "<style>" + _PANEL_CSS.replace("__TOP__", "54") + "</style>\n"
    '<div class="lvkit-props-panel" id="lvkitPropsPanel" hidden></div>\n'
    "<script>\n"
    "(function(){\n"
    "  try {\n"
    "    // Scoped to #stage's own (root) svg, same lookup the viewer's own\n"
    "    // zoom/pan script uses -- document order puts the root before any\n"
    "    // nested icon <svg> fragments the diagram draws, but scoping avoids\n"
    "    // relying on that.\n"
    "    var stageEl = document.getElementById('stage');\n"
    "    var svg = stageEl && stageEl.querySelector('svg');\n"
    "    var panel = document.getElementById('lvkitPropsPanel');\n"
    "    var btn = document.getElementById('__BTN_ID__');\n"
    "    if (!svg) return;\n"
    "\n"
    "    var props = null, struct = null, beforeProps = null, beforeStruct = null;\n"
    "    try {\n"
    "      if (svg.dataset.lvProperties)\n"
    "        props = JSON.parse(svg.dataset.lvProperties);\n"
    "    } catch (e) { props = null; }\n"
    "    try {\n"
    "      if (svg.dataset.lvStructure) struct = JSON.parse(svg.dataset.lvStructure);\n"
    "    } catch (e) { struct = null; }\n"
    + _PANEL_BODY_JS +
    "\n"
    "    if (btn) btn.addEventListener('click', function () {\n"
    "      if (!panel) return;\n"
    "      panel.hidden = !panel.hidden;\n"
    "      btn.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');\n"
    "    });\n"
    "  } catch (e) { /* properties chrome is optional -- never break the viewer */ }\n"
    "})();\n"
    "</script>"
).replace("__BTN_ID__", PROPERTIES_PANEL_BTN_ID)


# ---------------------------------------------------------------------------
# Diff-viewer variant: same button/popover VALUE rendering (_PANEL_BODY_JS),
# but sourced from the AFTER pane's dataset with the BEFORE pane's dataset
# supplied for highlighting -- "the single-VI property viewer, but with the
# modified items highlighted", not a separate changes-only list.
# ---------------------------------------------------------------------------

# Amber highlight for a changed row (the SAME --mod token the "modified"
# legend swatch/tag already use) + the button ring shown when >=1 shown value
# differs -- diff_viewer.html-only (--mod/--panel aren't relied on outside
# it); the render_viewer.html panel above never applies these classes.
# `.lvkit-prop-selected` is the STRONGER state a click on the matching
# property/structure CHANGES-list row applies (diff_viewer.html's
# revealPropertyRow, keyed by data-key == the change's raw field name) --
# a solid --mod fill + ring, clearly a step up from the passive tint every
# changed row already wears, so the clicked row pops even among several
# changed ones.
_DIFF_PANEL_EXTRA_CSS = """
  #__DIFF_BTN_ID__.lvkit-props-changed{
    box-shadow:0 0 0 2px var(--mod);border-color:var(--mod)}
  .lvkit-props-panel dt.lvkit-prop-changed,
  .lvkit-props-panel dd.lvkit-prop-changed{
    background:color-mix(in srgb, var(--mod) 22%, transparent);
    border-radius:3px;padding:1px 4px}
  .lvkit-props-panel dt.lvkit-prop-selected,
  .lvkit-props-panel dd.lvkit-prop-selected{
    background:color-mix(in srgb, var(--mod) 55%, transparent);
    outline:1px solid var(--mod);border-radius:3px;padding:1px 4px}
"""

DIFF_PROPERTIES_PANEL = (
    "<style>" + _PANEL_CSS.replace("__TOP__", "64")
    + _DIFF_PANEL_EXTRA_CSS.replace("__DIFF_BTN_ID__", DIFF_PROPERTIES_PANEL_BTN_ID)
    + "</style>\n"
    '<div class="lvkit-props-panel" id="lvkitDiffPropsPanel" hidden></div>\n'
    "<script>\n"
    "(function(){\n"
    "  try {\n"
    "    // Each pane's ROOT svg carries its own data-lv-properties/\n"
    "    // data-lv-structure (render/__init__.py's _vi_properties_data_attrs).\n"
    "    // The popover shows the AFTER pane's values (like the single-VI\n"
    "    // panel); the BEFORE pane's dataset is read ONLY to highlight rows\n"
    "    // that changed.\n"
    "    var beforePane = document.getElementById('beforePane');\n"
    "    var afterPane = document.getElementById('afterPane');\n"
    "    var beforeSvg = beforePane && beforePane.querySelector('svg');\n"
    "    var afterSvg = afterPane && afterPane.querySelector('svg');\n"
    "    var panel = document.getElementById('lvkitDiffPropsPanel');\n"
    "    var btn = document.getElementById('__DIFF_BTN_ID__');\n"
    "    if (!afterSvg) return;\n"
    "\n"
    "    var props = null, struct = null, beforeProps = null, beforeStruct = null;\n"
    "    try {\n"
    "      if (afterSvg.dataset.lvProperties)\n"
    "        props = JSON.parse(afterSvg.dataset.lvProperties);\n"
    "    } catch (e) { props = null; }\n"
    "    try {\n"
    "      if (afterSvg.dataset.lvStructure)\n"
    "        struct = JSON.parse(afterSvg.dataset.lvStructure);\n"
    "    } catch (e) { struct = null; }\n"
    "    if (beforeSvg) {\n"
    "      try {\n"
    "        if (beforeSvg.dataset.lvProperties)\n"
    "          beforeProps = JSON.parse(beforeSvg.dataset.lvProperties);\n"
    "      } catch (e) { beforeProps = null; }\n"
    "      try {\n"
    "        if (beforeSvg.dataset.lvStructure)\n"
    "          beforeStruct = JSON.parse(beforeSvg.dataset.lvStructure);\n"
    "      } catch (e) { beforeStruct = null; }\n"
    "    }\n"
    + _PANEL_BODY_JS +
    "\n"
    "    // Ring the button amber iff >=1 shown value differs from BEFORE.\n"
    "    if (btn) {\n"
    "      if (changedCount > 0) {\n"
    "        btn.classList.add('lvkit-props-changed');\n"
    "        btn.title = 'VI properties changed (' + changedCount"
    " + ') \\u2014 click to view';\n"
    "      } else {\n"
    "        btn.title = 'No VI property changes';\n"
    "      }\n"
    "      btn.addEventListener('click', function () {\n"
    "        if (!panel) return;\n"
    "        panel.hidden = !panel.hidden;\n"
    "        btn.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');\n"
    "      });\n"
    "    }\n"
    "  } catch (e) {\n"
    "    /* diff properties chrome is optional -- never break the viewer */\n"
    "  }\n"
    "})();\n"
    "</script>"
).replace("__DIFF_BTN_ID__", DIFF_PROPERTIES_PANEL_BTN_ID)

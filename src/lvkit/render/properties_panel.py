"""VI-properties chrome for the render viewer (task #19): a single-glyph
toolbar button + a collapsible "Properties" popover, driven ENTIRELY from the
root ``<svg>``'s ``data-lv-properties``/``data-lv-health`` JSON attributes
(see ``render/__init__.py``'s ``_vi_properties_data_attrs``) — the SVG is the
single carrier, so the same data a host reading the raw SVG (e.g. the VS Code
extension) would parse is what this chrome reads too. The button + popover
are the SOLE properties surface — there is deliberately no always-visible
header summary (chips); header space is tight, and the popover is one click
away, so a second summary would only duplicate it.

Also supplies the DIFF viewer's equivalent (``DIFF_PROPERTIES_BUTTON``/
``DIFF_PROPERTIES_PANEL``): the SAME popover — grouped VALUES (Version/
Execution/Window/Toolbar/Instance/Kind/Health), not a bespoke changes-only
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
  - The toggle BUTTON stays in the toolbar and follows the viewer/system theme
    like every other toolbar button. The POPOVER, however, is part of the VI
    VIEW SPACE: VI properties are a VI-level construct, so the panel is pinned
    within the diagram stage (moved into ``.stage-wrap`` by the script, like the
    ▦ connector-pane reveal) and themed to the DIAGRAM via the ``--d*`` tokens
    (which follow the ◐/☀/☾ ``data-theme`` toggle — defined on :root by both
    templates alongside ``--canvas``). The diff-viewer variant additionally
    leans on ``diff_viewer.html``'s own ``--mod`` token (the SAME amber the
    "modified" legend swatch/tag already use) for its changed-row/changed-ring
    colour, so a changed VI property reads with the same colour language as a
    changed node/wire.

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

# Shared panel-box CSS (position/size/typography) — ONE definition used by both
# PROPERTIES_PANEL and DIFF_PROPERTIES_PANEL. The popover is part of the VI VIEW
# SPACE: each viewer's script moves it into the positioned ``.stage-wrap`` so it
# pins within the diagram stage's top-right (like the ▦ connector-pane reveal),
# and it is themed to the DIAGRAM via the ``--d*`` tokens (which follow the
# ◐/☀/☾ data-theme toggle, defined on :root by both templates) — VI properties
# are a VI-level construct, shown over the VI, not window chrome.
_PANEL_CSS = """
  .lvkit-props-panel{position:absolute;top:8px;right:8px;left:auto;z-index:70;
    width:300px;max-width:calc(100% - 32px);max-height:calc(100% - 16px);
    overflow:auto;background:var(--dpanel);border:1px solid var(--dline);
    border-radius:4px;color:var(--dfg);
    box-shadow:0 2px 12px rgba(0,0,0,.28);padding:10px 12px;font-size:12px}
  .lvkit-props-panel[hidden]{display:none}
  .lvkit-props-panel h4{margin:10px 0 4px;font-size:11px;text-transform:uppercase;
    letter-spacing:.03em;color:var(--dfg-muted)}
  .lvkit-props-panel h4:first-child{margin-top:0}
  .lvkit-props-panel .prop-rows{display:flex;flex-direction:column}
  .lvkit-props-panel .prop-row{display:flex;justify-content:space-between;gap:10px;
    padding:1px 5px;border-radius:var(--hl-r,6px)}
  .lvkit-props-panel .prop-k{color:var(--dfg-muted)}
  .lvkit-props-panel .prop-v{text-align:right;overflow-wrap:anywhere}
  .lvkit-props-empty{color:var(--dfg-muted)}
"""

# Shared row/group/buildPanel JS — the ONE value-rendering implementation
# both panels use. Grouped like `describe` (Version / Execution / Window /
# Toolbar / Instance / Kind / Health): EVERY field shows (unfiltered), like
# the diagram shows unchanged nodes -- "see every value, changed ones
# highlighted" -- not just the non-default ones (that filter used to hide
# most of a VI's actual properties from the single-VI popover).
#
# `beforeProps`/`beforeHealth` (declared by each panel's own preamble, see
# PROPERTIES_PANEL/DIFF_PROPERTIES_PANEL below) drive an OPTIONAL highlight:
# when set, a shown field whose value differs from the matching before-side
# value gets a `.lvkit-prop-changed` class. The single-VI panel's preamble
# always sets them to null, so `changed` is always false there and the class
# is never applied — this body is byte-identical shared code, not a
# diff-only fork.
_PANEL_BODY_JS = """
    var changedCount = 0;
    function row(container, key, value, changed) {
      // ONE element per property (key + value), so the change highlight covers
      // the WHOLE ROW as a single ring -- not two boxes (dt + dd) per row.
      var r = document.createElement("div");
      r.className = "prop-row" + (changed ? " lvkit-prop-changed" : "");
      r.dataset.key = key;
      var k = document.createElement("span");
      k.className = "prop-k"; k.textContent = key;
      var v = document.createElement("span");
      v.className = "prop-v"; v.textContent = String(value);
      r.appendChild(k); r.appendChild(v);
      if (changed) changedCount++;
      container.appendChild(r);
    }
    function group(title, obj, beforeObj) {
      if (!obj) return null;
      var keys = Object.keys(obj).sort();
      if (!keys.length) return null;
      var frag = document.createDocumentFragment();
      var h = document.createElement("h4");
      h.textContent = title;
      frag.appendChild(h);
      var dl = document.createElement("div");
      dl.className = "prop-rows";
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
          ["Kind", props.kind, beforeProps ? beforeProps.kind : null],
        ].forEach(function (t) {
          var frag = group(t[0], t[1], t[2]);
          if (frag) { panel.appendChild(frag); any = true; }
        });
      }
      var healthFrag = group("Health", health, beforeHealth);
      if (healthFrag) { panel.appendChild(healthFrag); any = true; }
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
    "<style>" + _PANEL_CSS + "</style>\n"
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
    "    // Move the popover INTO the diagram view space (the positioned\n"
    "    // .stage-wrap), so it pins over the stage's top-right like the VI's\n"
    "    // connector-pane reveal -- a VI-level construct shown over the VI.\n"
    "    var wrap = stageEl.closest('.stage-wrap');\n"
    "    if (wrap && panel) wrap.appendChild(panel);\n"
    "\n"
    "    var props = null, health = null, beforeProps = null, beforeHealth = null;\n"
    "    try {\n"
    "      if (svg.dataset.lvProperties)\n"
    "        props = JSON.parse(svg.dataset.lvProperties);\n"
    "    } catch (e) { props = null; }\n"
    "    try {\n"
    "      if (svg.dataset.lvHealth) health = JSON.parse(svg.dataset.lvHealth);\n"
    "    } catch (e) { health = null; }\n"
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
# property/health CHANGES-list row applies (diff_viewer.html's
# revealPropertyRow, keyed by data-key == the change's raw field name) --
# a solid --mod fill + ring, clearly a step up from the passive tint every
# changed row already wears, so the clicked row pops even among several
# changed ones.
_DIFF_PANEL_EXTRA_CSS = """
  #__DIFF_BTN_ID__.lvkit-props-changed{
    box-shadow:0 0 0 2px var(--mod);border-color:var(--mod)}
  /* WHOLE-ROW change highlight -- ONE rounded rectangle per property (not a box
     per cell). Passive changed = NO fill, a thin change-coloured border RING
     (box-shadow spread, no layout shift); selected = a faint fill + a PULSING
     ring (grows on the beat -- the reliable HTML analog of the diagram's selpulse
     stroke-width pulse) + the change-coloured glow. Radius is the SHARED --hl-r,
     so the diagram, connector pane and properties round identically from one
     place. HTML can't drive the SVG selpulse, so lvselpulse is its lone analog. */
  .lvkit-props-panel .prop-row.lvkit-prop-changed{
    background:none;box-shadow:0 0 0 1.5px var(--mod)}
  .lvkit-props-panel .prop-row.lvkit-prop-selected{
    background:color-mix(in srgb, var(--mod) 20%, transparent);
    animation:lvselpulse 1.25s ease-in-out infinite}
  @keyframes lvselpulse{
    0%,100%{box-shadow:0 0 0 2px var(--mod), 0 0 3px 1px var(--mod)}
    50%{box-shadow:0 0 0 4px var(--mod), 0 0 9px 2px var(--mod)}}
  /* Spotlight: dim every OTHER changed row when one is picked (like .stage.has-sel). */
  .lvkit-props-panel.lvkit-has-sel
    .prop-row.lvkit-prop-changed:not(.lvkit-prop-selected){opacity:.32}
  /* Change number: the SAME bold, change-coloured, HALOED look as the diagram's
     .hl-num -- an HTML text-shadow halo stands in for the SVG stroke halo. */
  .lvkit-props-panel .prop-row .lvkit-prop-num{
    font:bold 13px system-ui,sans-serif;margin-right:6px;color:var(--mod);
    font-variant-numeric:tabular-nums;
    text-shadow:0 0 2px var(--dpanel),0 0 2px var(--dpanel),0 0 2px var(--dpanel)}
"""

DIFF_PROPERTIES_PANEL = (
    "<style>" + _PANEL_CSS
    + _DIFF_PANEL_EXTRA_CSS.replace("__DIFF_BTN_ID__", DIFF_PROPERTIES_PANEL_BTN_ID)
    + "</style>\n"
    '<div class="lvkit-props-panel" id="lvkitDiffPropsPanel" hidden></div>\n'
    "<script>\n"
    "(function(){\n"
    "  try {\n"
    "    // Each pane's ROOT svg carries its own data-lv-properties/\n"
    "    // data-lv-health (render/__init__.py's _vi_properties_data_attrs).\n"
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
    "    // Move the popover INTO the diagram view space (the positioned\n"
    "    // .stage-wrap), pinned over the stage's top-right like the VI's\n"
    "    // connector-pane reveal -- VI properties shown over the VI itself.\n"
    "    var wrap = afterPane.closest('.stage-wrap');\n"
    "    if (wrap && panel) wrap.appendChild(panel);\n"
    "\n"
    "    var props = null, health = null, beforeProps = null, beforeHealth = null;\n"
    "    try {\n"
    "      if (afterSvg.dataset.lvProperties)\n"
    "        props = JSON.parse(afterSvg.dataset.lvProperties);\n"
    "    } catch (e) { props = null; }\n"
    "    try {\n"
    "      if (afterSvg.dataset.lvHealth)\n"
    "        health = JSON.parse(afterSvg.dataset.lvHealth);\n"
    "    } catch (e) { health = null; }\n"
    "    if (beforeSvg) {\n"
    "      try {\n"
    "        if (beforeSvg.dataset.lvProperties)\n"
    "          beforeProps = JSON.parse(beforeSvg.dataset.lvProperties);\n"
    "      } catch (e) { beforeProps = null; }\n"
    "      try {\n"
    "        if (beforeSvg.dataset.lvHealth)\n"
    "          beforeHealth = JSON.parse(beforeSvg.dataset.lvHealth);\n"
    "      } catch (e) { beforeHealth = null; }\n"
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
    "        // A manual open/close is NOT selection-driven, so a later\n"
    "        // change-deselect must not auto-close it (see clearSel).\n"
    "        window.lvkitPropPanelBySel = false;\n"
    "        // MUTUALLY EXCLUSIVE with the ▦ connector pane (shared view space).\n"
    "        if (panel.hidden && window.lvkitCloseConnectorPanes)\n"
    "          window.lvkitCloseConnectorPanes();\n"
    "        panel.hidden = !panel.hidden;\n"
    "        btn.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');\n"
    "      });\n"
    "    }\n"
    "    // Let the connector pane (and a selection-driven open) close us.\n"
    "    window.lvkitCloseProps = function () {\n"
    "      if (panel && !panel.hidden) {\n"
    "        panel.hidden = true; window.lvkitPropPanelBySel = false;\n"
    "        if (btn) btn.setAttribute('aria-expanded', 'false');\n"
    "      }\n"
    "    };\n"
    "  } catch (e) {\n"
    "    /* diff properties chrome is optional -- never break the viewer */\n"
    "  }\n"
    "})();\n"
    "</script>"
).replace("__DIFF_BTN_ID__", DIFF_PROPERTIES_PANEL_BTN_ID)

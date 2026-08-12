"""VI-properties chrome for the render viewer (task #19): status chips in the
header + a collapsible "Properties" panel, both driven ENTIRELY from the root
``<svg>``'s ``data-lv-properties``/``data-lv-structure`` JSON attributes (see
``render/__init__.py``'s ``_vi_properties_data_attrs``) — the SVG is the single
carrier, so the same data a host reading the raw SVG (e.g. the VS Code
extension) would parse is what this chrome reads too.

CLEAN-ROOM + THEME rules (hard, per task #19):
  - Chips/panel are PLAIN UI — text + CSS only. NEVER an NI-derived glyph (no
    LabVIEW broken-run-arrow, no drawn lock icon mimicking LabVIEW chrome).
  - They are viewer CHROME, so they follow the viewer/system theme via plain
    ``@media (prefers-color-scheme: dark)`` — same tokens the rest of
    ``render_viewer.html`` already defines (``--panel``/``--line``/``--fg``/
    ``--fg-muted``/``--btn``). They must NEVER react to ``data-theme`` (that
    toggle is diagram-only, see the ``render_viewer.html`` design-law
    comment) and never draw anything into the SVG itself.

Same shared-placeholder-injection pattern as :mod:`lvkit.render.theme_control`
and :mod:`lvkit.render.help_tip`: this module supplies static HTML/CSS/JS
blobs; ``render_viewer.py`` staples them into the template via
``__PROPERTIES_BTN__``/``__PROPERTIES_PANEL__``.
"""

from __future__ import annotations

__all__ = ["PROPERTIES_BUTTON", "PROPERTIES_PANEL", "PROPERTIES_PANEL_BTN_ID"]

# The single element id the toggle button and its script agree on.
PROPERTIES_PANEL_BTN_ID = "lvkitPropsBtn"

# A plain text toolbar button (matches the "fit" zoom button's footprint/style
# — no icon glyph, so there is no risk of it reading as an NI-derived symbol).
PROPERTIES_BUTTON = (
    f'<button id="{PROPERTIES_PANEL_BTN_ID}" type="button" '
    'title="Show/hide VI properties" '
    'aria-label="Toggle VI properties panel" aria-expanded="false">Properties</button>'
)

# Bundles: the chip/panel CSS, the (initially empty/hidden) panel container,
# and the script that populates chips + panel from the embedded SVG's dataset
# and wires the toggle button. Injected once, near end-of-body, same spot as
# __HELP_TIP__.
PROPERTIES_PANEL = """<style>
  /* Chrome-only tokens (already defined on :root by render_viewer.html) —
     these rules never reference data-theme, only the page's own --panel/
     --line/--fg/--fg-muted/--btn, which already flip with
     prefers-color-scheme. */
  .lvkit-chips{display:inline-flex;gap:4px;flex-wrap:wrap;align-items:center}
  .lvkit-chip{font-size:11px;line-height:1;padding:3px 8px;border-radius:999px;
    background:var(--btn);border:1px solid var(--line);color:var(--fg-muted);
    white-space:nowrap}
  .lvkit-props-panel{position:fixed;top:54px;right:16px;z-index:6;width:300px;
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
</style>
<div class="lvkit-props-panel" id="lvkitPropsPanel" hidden></div>
<script>
(function(){
  try {
    // Scoped to #stage's own (root) svg, same lookup the viewer's own zoom/pan
    // script uses -- document order puts the root before any nested icon
    // <svg> fragments the diagram draws, but scoping avoids relying on that.
    var stageEl = document.getElementById('stage');
    var svg = stageEl && stageEl.querySelector('svg');
    var chipsEl = document.getElementById('lvkitChips');
    var panel = document.getElementById('lvkitPropsPanel');
    var btn = document.getElementById('__BTN_ID__');
    if (!svg) return;

    var props = null, struct = null;
    try {
      if (svg.dataset.lvProperties) props = JSON.parse(svg.dataset.lvProperties);
    } catch (e) { props = null; }
    try {
      if (svg.dataset.lvStructure) struct = JSON.parse(svg.dataset.lvStructure);
    } catch (e) { struct = null; }

    // ── Status chips (header, next to the title) ─────────────────────────
    if (chipsEl) {
      var labels = [];
      if (props && props.lock_state === "locked") labels.push("Locked");
      if (props && props.lock_state === "password_protected")
        labels.push("Password Protected");
      if (struct && struct.is_broken) labels.push("Broken");
      if (props && props.execution && props.execution.reentrant)
        labels.push("Reentrant");
      if (struct && struct.is_typedef) labels.push("Typedef");
      labels.forEach(function (label) {
        var s = document.createElement("span");
        s.className = "lvkit-chip";
        s.textContent = label;
        chipsEl.appendChild(s);
      });
    }

    // ── Collapsible Properties panel ──────────────────────────────────────
    // Grouped like `describe` (version / execution / window / toolbar /
    // instance / structure): only non-default (truthy/non-null) fields show,
    // so the common case stays terse -- same convention as
    // graph/describe.py's _describe_properties/_describe_structure.
    function row(dl, key, value) {
      var dt = document.createElement("dt");
      dt.textContent = key;
      var dd = document.createElement("dd");
      dd.textContent = String(value);
      dl.appendChild(dt);
      dl.appendChild(dd);
    }
    function group(title, obj, always) {
      if (!obj) return null;
      var keys = Object.keys(obj)
        .filter(function (k) {
          var v = obj[k];
          return always && always.indexOf(k) >= 0
            ? v !== null && v !== undefined
            : v !== false && v !== null && v !== undefined;
        })
        .sort();
      if (!keys.length) return null;
      var frag = document.createDocumentFragment();
      var h = document.createElement("h4");
      h.textContent = title;
      frag.appendChild(h);
      var dl = document.createElement("dl");
      keys.forEach(function (k) { row(dl, k, obj[k]); });
      frag.appendChild(dl);
      return frag;
    }
    function buildPanel() {
      if (!panel) return;
      panel.innerHTML = "";
      var any = false;
      if (props) {
        var top = group(
          "Version", { lock_state: props.lock_state, lv_version: props.lv_version,
                        vi_type: props.vi_type },
          ["lock_state"],
        );
        if (top) { panel.appendChild(top); any = true; }
        [
          ["Execution", props.execution], ["Window", props.window],
          ["Toolbar", props.toolbar], ["Instance", props.instance],
        ].forEach(function (pair) {
          var frag = group(pair[0], pair[1]);
          if (frag) { panel.appendChild(frag); any = true; }
        });
      }
      var structFrag = group("Structure", struct);
      if (structFrag) { panel.appendChild(structFrag); any = true; }
      if (!any) {
        var p = document.createElement("div");
        p.className = "lvkit-props-empty";
        p.textContent = "(no notable properties)";
        panel.appendChild(p);
      }
    }
    buildPanel();

    if (btn) btn.addEventListener("click", function () {
      if (!panel) return;
      panel.hidden = !panel.hidden;
      btn.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
    });
  } catch (e) { /* properties chrome is optional -- never break the viewer */ }
})();
</script>""".replace("__BTN_ID__", PROPERTIES_PANEL_BTN_ID)

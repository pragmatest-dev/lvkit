"""Image-only dark/light theming shared by the sampler and the Cloud Run page.

A rendered SVG produced with :func:`lvkit.render.style.css_var_theme` references
``var(--lv-<role>, <light-hex>)`` for every color. With no CSS present the
light-hex FALLBACK renders — so a diagram is light by default. To make a diagram
DARK, we set ``data-lv-theme="dark"`` on the ``<svg>`` element and redefine the
``--lv-*`` custom properties on that element; the values cascade to its
descendants. Scoping to ``svg[data-lv-theme="dark"]`` means the toggle recolors
ONLY the diagrams — the host page's own background/header/chrome never move.

Both the sampler (``scripts/render_gallery.py``) and the Cloud Run page
(``deploy/cloudrun``) import from here so the palette and the toggle behavior
stay identical and can't drift. The light values come from ``DEFAULT_THEME``
(via the SVG fallbacks); the dark values are :data:`DARK_PALETTE`.
"""

from __future__ import annotations

import dataclasses

from .style import DEFAULT_THEME, Theme

# Dark value for every hex-color Theme field. Dark mode is OUR design (LabVIEW is
# light-only), chosen for legibility on a dark canvas while keeping semantic
# colors semantic (red coercion dot, green/red case borders, blue N/i). Every
# hex field of Theme MUST have an entry — theme_style_block() raises if one is
# missing, so a new Theme color can't silently ship untuned.
DARK_PALETTE: dict[str, str] = {
    "canvas": "#1b1c1e",
    "struct_border": "#cdcdcd",
    "prim_fill": "#34301e",
    "prim_stroke": "#d9a93a",
    "prim_text": "#f2ecd8",
    "term_fill": "#2d2a20",
    "const_fill": "#26262b",
    "const_text": "#ededed",
    "loop_term": "#6b8cff",
    "loop_term_fill": "#34331d",
    "loop_term_text": "#f2ecd8",
    "cond_stop": "#ff5b5b",
    "cond_continue": "#5bd06a",
    "subvi_fill": "#2a2c26",
    "subvi_stroke": "#a9ac8f",
    "subvi_text": "#e9e9e0",
    "case_bar_fill": "#2c2a22",
    "case_bar_text": "#d8d4c0",
    "case_no_error_border": "#46c85a",
    "case_error_border": "#ff5b5b",
    "disabled_mask": "#5a5a5a",
    "selector_fill": "#253020",
    "selector_stroke": "#7fbf5a",
    "selector_text": "#bfe0a5",
    "sr_fill": "#3a3a3c",
    "sr_stroke": "#a8a8a8",
    "tunnel_border": "#b8b49a",
    "coercion_dot": "#ff4d4d",
    "fp_panel": "#34343a",
    "fp_value_fill": "#26262b",
    "fp_value_text": "#e6e6e6",
    "fp_index_fill": "#3a3a3c",
    "localvar_fill": "#26262b",
    "localvar_stroke": "#b8b49a",
    "localvar_text": "#ededed",
    "text": "#e8e8e0",
    "pane_type_text": "#9a9a9a",
    "wire_float": "#f5972f",
    "wire_int": "#6b8cff",
    "wire_bool": "#5cc14e",
    "wire_string": "#f284bd",
    "wire_path": "#38b6b6",
    "wire_cluster": "#c8965a",
    "wire_refnum": "#3f9e58",
    "wire_error": "#cdb03e",
    "wire_variant": "#c452c4",
    "wire_default": "#8a8a8a",
}


def _hex_fields(theme: Theme) -> list[str]:
    """Names of every HEX-COLOR field on ``Theme`` (excludes ``wire_casing``, a
    float) — the same predicate ``css_var_theme()`` uses, so the CSS covers
    exactly the set of vars an inlined var-theme SVG expects."""
    return [
        f.name for f in dataclasses.fields(theme)
        if isinstance(getattr(theme, f.name), str)
        and getattr(theme, f.name).startswith("#")
    ]


def theme_style_block() -> str:
    """Bare CSS (no ``<style>`` wrapper) that darkens a diagram when its ``<svg>``
    carries ``data-lv-theme="dark"``. Light is the SVG's own var() fallback, so
    only the dark override is emitted — and it is scoped to ``svg[...]`` so the
    host page's chrome is never touched. Raises if ``DARK_PALETTE`` is missing
    any hex field (a new color can't ship without a dark value)."""
    fields = _hex_fields(DEFAULT_THEME)
    missing = [n for n in fields if n not in DARK_PALETTE]
    if missing:
        raise ValueError(f"DARK_PALETTE missing entries for: {missing}")
    dark_vars = "\n".join(
        f"  --lv-{name.replace('_', '-')}: {DARK_PALETTE[name]};" for name in fields
    )
    return f'svg[data-lv-theme="dark"] {{\n{dark_vars}\n}}'


# The visible toggle button — text is set by the control script.
THEME_TOGGLE_BUTTON = (
    '<button id="lv-theme-toggle" type="button">Dark diagrams</button>'
)

# Flips ONLY the diagrams: sets/removes data-lv-theme="dark" on every <svg>,
# persists the choice, keeps the button label in sync, and (via a
# MutationObserver) applies the current choice to any <svg> added later — so the
# Cloud Run page's fetched-and-inserted diagram themes correctly too. The host
# page's own colors are untouched.
THEME_TOGGLE_SCRIPT = """<script>
(function(){
  var KEY = "lv-diagram-theme";
  function dark(){
    try { return localStorage.getItem(KEY) === "dark"; }
    catch(e){ return false; }
  }
  function applyTo(svg){
    if (dark()) svg.setAttribute("data-lv-theme", "dark");
    else svg.removeAttribute("data-lv-theme");
  }
  function applyAll(){
    var svgs = document.querySelectorAll("svg");
    for (var i=0;i<svgs.length;i++) applyTo(svgs[i]);
  }
  function sync(btn){
    if (btn) btn.textContent = dark() ? "Light diagrams" : "Dark diagrams";
  }
  var btn = document.getElementById("lv-theme-toggle");
  if (btn) btn.addEventListener("click", function(){
    try { localStorage.setItem(KEY, dark() ? "light" : "dark"); } catch(e){}
    applyAll(); sync(btn);
  });
  // Re-apply to <svg> nodes inserted after load (e.g. the Cloud Run render).
  new MutationObserver(function(muts){
    for (var m=0;m<muts.length;m++){
      var added = muts[m].addedNodes;
      for (var n=0;n<added.length;n++){
        var el = added[n];
        if (el.nodeType !== 1) continue;
        if (el.tagName && el.tagName.toLowerCase() === "svg") applyTo(el);
        else if (el.querySelectorAll) {
          var s = el.querySelectorAll("svg");
          for (var k=0;k<s.length;k++) applyTo(s[k]);
        }
      }
    }
  }).observe(document.documentElement, {childList:true, subtree:true});
  applyAll(); sync(btn);
})();
</script>"""

#!/usr/bin/env python3
"""Render a fixed corpus of sample VIs with the block-diagram renderer and
build a single self-contained HTML gallery for visual review.

Run it:

    uv run python scripts/render_gallery.py

Output (printed at the end as an absolute path):

    outputs/gallery/index.html   <- open this in a browser
    outputs/gallery/<name>.svg   <- one SVG per VI (also open-able directly)

The gallery embeds each SVG inline, so there is **no rasterizer dependency**
(no cairosvg / Pillow) — browsers and most editors render SVG natively. Pass
``--out DIR`` to write somewhere else.

The corpus lives in ``CORPUS`` below. Sample ``.vi`` files are external test
data under ``samples/`` (gitignored); any VI that is missing locally is simply
skipped, and any that fails to extract is reported — neither aborts the run.
"""

from __future__ import annotations

import argparse
import dataclasses
import html
from dataclasses import dataclass
from pathlib import Path

from lvkit.render import render_vi_file
from lvkit.render.style import DEFAULT_THEME, Theme, css_var_theme

REPO_ROOT = Path(__file__).resolve().parent.parent

# Dark-mode counterpart of every hex-color field on ``Theme`` (task #26,
# first cut — the reviewer tunes these after inspecting rasterized before/
# after shots; only the WIRING here is final). Semantic colors keep their
# hue (case borders, coercion dot, wire-type colors) but are brightened for
# dark backgrounds; everything else follows the fill<->paired-text pattern
# ``draw.py``/``glyph.py`` now use throughout.
_DARK_PALETTE: dict[str, str] = {
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
    "subvi_fill": "#2a2c26",
    "subvi_stroke": "#a9ac8f",
    "subvi_text": "#e9e9e0",
    "case_bar_fill": "#2c2a22",
    "case_bar_text": "#d8d4c0",
    "case_no_error_border": "#46c85a",
    "case_error_border": "#ff5b5b",
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
    # Not in the reviewer's first-cut list (that list predates the
    # connector-panel's secondary/muted text role) — picked to be legible
    # against a dark ``canvas``; flagged for the reviewer to tune alongside
    # everything else.
    "pane_type_text": "#9a9a9a",
    "wire_float": "#f5972f",
    "wire_int": "#6b8cff",
    "wire_bool": "#5cc14e",
    "wire_string": "#f284bd",
    "wire_path": "#38b6b6",
    "wire_cluster": "#c8965a",
    "wire_error": "#cdb03e",
    "wire_variant": "#c452c4",
    "wire_default": "#8a8a8a",
}


def _hex_fields(theme: Theme) -> list[str]:
    """Names of every HEX-COLOR field on ``Theme`` (excludes ``wire_casing``,
    a float) — the same predicate ``css_var_theme()`` uses, so the CSS below
    always covers exactly the set of vars an inlined var-theme SVG expects."""
    return [
        f.name for f in dataclasses.fields(theme)
        if isinstance(getattr(theme, f.name), str)
        and getattr(theme, f.name).startswith("#")
    ]


def _theme_css_block(theme: Theme, fields: list[str]) -> str:
    return "\n".join(
        f"  --lv-{name.replace('_', '-')}: {getattr(theme, name)};" for name in fields
    )


def _theme_style_block() -> str:
    """One ``<style>`` block: light vars (generated from ``DEFAULT_THEME``, so
    they can never drift from the renderer's real light palette) under
    ``:root``, dark vars under ``:root[data-theme="dark"]`` (explicit toggle)
    AND ``@media (prefers-color-scheme: dark) { :root:not([data-theme]) {...} }``
    (follows the OS when the user hasn't toggled)."""
    fields = _hex_fields(DEFAULT_THEME)
    missing = [n for n in fields if n not in _DARK_PALETTE]
    if missing:
        raise ValueError(f"_DARK_PALETTE missing entries for: {missing}")
    light_css = _theme_css_block(DEFAULT_THEME, fields)
    dark_css = "\n".join(
        f"  --lv-{name.replace('_', '-')}: {_DARK_PALETTE[name]};" for name in fields
    )
    return (
        f":root {{\n{light_css}\n}}\n"
        f':root[data-theme="dark"] {{\n{dark_css}\n}}\n'
        "@media (prefers-color-scheme: dark) {\n"
        "  :root:not([data-theme]) {\n"
        f"{dark_css}\n"
        "  }\n"
        "}"
    )


@dataclass(frozen=True)
class Sample:
    name: str
    path: str
    note: str


# Curated to exercise the renderer's breadth: structures, wire types, icons,
# and — for the error/variant work — mustard error wires + purple variants.
CORPUS: list[Sample] = [
    Sample("array_average", "experiments/lv-renderer/gallery/array average 1.vi",
           "For-Loop · arithmetic · auto-index array · coercion dot — reference VI"),
    Sample("daqmx_out", "samples/DAQmx-Digital-IO/Out.vi",
           "While loop · SubVI chain · mustard error-cluster wire · typed constants"),
    Sample("daqmx_in", "samples/DAQmx-Digital-IO/In.vi",
           "Flat sequence · SubVI chain · error propagation"),
    Sample("easyxml_build", "samples/JKI-EasyXML/Build.vi",
           "Case structures · many SubVIs · build script"),
    Sample("easy_parse_xml", "samples/JKI-EasyXML/Source/Easy Parse XML.vi",
           "Dense dataflow · nested cases · SubVIs · clusters"),
    Sample("xml_loop_recursion",
           "samples/JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi",
           "Nested cases · recursion · purple variant + mustard error wires"),
    Sample("cluster_to_array",
           "samples/JKI-EasyXML/Source/OpenG Derivative/"
           "Cluster to Array of VData--EasyXML.vi",
           "Arrays + clusters · purple Variant terminals + solid-purple constant"),
    Sample("vitester_build", "samples/JKI-VI-Tester/source/build.vi",
           "LabVIEW class / project-plugin (OOP) dataflow"),
    Sample(
        "stacked_sequence",
        "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/string/"
        "string.llb/Number to Proper Engl Text__ogtk.vi",
        "Stacked sequence (3 frames) · click the ◄ index ► selector to flip",
    ),
]


def _card(sample: Sample, svg: str) -> str:
    return (
        '<figure class="card">'
        f'<figcaption><b>{html.escape(sample.name)}</b>'
        f'<span>{html.escape(sample.note)}</span></figcaption>'
        f'<div class="stage">{svg}</div>'
        '</figure>'
    )


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>lvkit renderer — VI gallery</title>
{theme_init_script}
<style>
:root {{ --bg:#f4f2ec; --panel:#fff; --ink:#23211c; --muted:#6f6a5d;
  --line:#e0dccf; }}
:root[data-theme="dark"] {{ --bg:#1b1a17; --panel:#232220; --ink:#e9e6dd;
  --muted:#9c968a; --line:#3a382f; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme]) {{ --bg:#1b1a17; --panel:#232220; --ink:#e9e6dd;
    --muted:#9c968a; --line:#3a382f; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui,sans-serif; }}
header {{ padding:24px 28px 8px; display:flex; justify-content:space-between;
  align-items:flex-start; gap:16px; }}
header h1 {{ margin:0 0 4px; font-size:20px; }}
header p {{ margin:0; color:var(--muted); font-size:13px; }}
header code {{ background:var(--panel); border:1px solid var(--line);
  padding:1px 5px; border-radius:4px; }}
#theme-toggle {{ flex:none; font:13px/1.4 system-ui,sans-serif; padding:6px 12px;
  border-radius:6px; border:1px solid var(--line); background:var(--panel);
  color:var(--ink); cursor:pointer; }}
#theme-toggle:hover {{ border-color:var(--muted); }}
.grid {{ display:flex; flex-direction:column; gap:20px; padding:20px 28px 48px; }}
.card {{ margin:0; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; overflow:hidden; }}
figcaption {{ padding:10px 14px; border-bottom:1px solid var(--line);
  display:flex; gap:12px; align-items:baseline; }}
figcaption b {{ font-size:14px; }}
figcaption span {{ color:var(--muted); font-size:12px; }}
.stage {{ padding:14px; overflow:hidden; cursor:grab; touch-action:none; }}
.stage.grabbing {{ cursor:grabbing; }}
.stage svg {{ max-width:100%; height:auto; display:block; }}
.fail {{ color:#c0392b; padding:10px 14px; font-size:13px; }}
</style>
<style>
{theme_vars_css}
</style>
<header>
  <div>
    <h1>lvkit renderer — VI gallery</h1>
    <p>{count} VIs · regenerate with
    <code>uv run python scripts/render_gallery.py</code>
    · <code>ctrl/⌘ + scroll</code> to zoom · drag to pan · double-click to reset
    · click a case's <code>◄ value ▼ ►</code> selector to flip frames</p>
  </div>
  <button id="theme-toggle" type="button">Dark mode</button>
</header>
<div class="grid">
{cards}
</div>
"""


# Per-diagram pan/zoom by editing the SVG's own viewBox (NOT a CSS transform,
# which rasterizes and blurs on zoom) — the browser re-renders vectors crisply
# at every level. Kept out of _PAGE so raw JS braces don't fight str.format.
# ctrl/⌘+wheel zooms toward the cursor; plain wheel is left alone so the page
# scrolls; drag pans; double-click resets. Zoom preserves the viewBox aspect so
# the on-screen box stays stable and screen->user mapping never drifts.
_SCRIPT = """
<script>/*<![CDATA[*/
(function(){
  document.querySelectorAll('.stage').forEach(function(stage){
    var svg = stage.querySelector('svg');
    if(!svg || !svg.getAttribute('viewBox')) return;
    var vb0 = svg.getAttribute('viewBox').split(/\\s+/).map(Number);
    var vb = vb0.slice();
    var aspect = vb0[3]/vb0[2];
    var minW = vb0[2]/40, maxW = vb0[2]*2;
    function set(){ svg.setAttribute('viewBox', vb[0]+' '+vb[1]+' '+vb[2]+' '+vb[3]); }
    stage.addEventListener('wheel', function(e){
      if(!(e.ctrlKey || e.metaKey)) return;   // plain wheel scrolls the page
      e.preventDefault();
      var r = svg.getBoundingClientRect();
      var fx=(e.clientX-r.left)/r.width, fy=(e.clientY-r.top)/r.height;
      var ux=vb[0]+fx*vb[2], uy=vb[1]+fy*vb[3];
      var f=Math.exp(-e.deltaY*0.0015);
      var nw=Math.min(maxW, Math.max(minW, vb[2]/f)), nh=nw*aspect;
      vb[2]=nw; vb[3]=nh; vb[0]=ux-fx*nw; vb[1]=uy-fy*nh; set();
    }, {passive:false});
    var drag=false, sx=0, sy=0, sox=0, soy=0;
    stage.addEventListener('pointerdown', function(e){
      if(e.button!==0) return;
      if(e.target.closest('.lv-selector,.lv-option')) return;
      drag=true; sx=e.clientX; sy=e.clientY; sox=vb[0]; soy=vb[1];
      stage.classList.add('grabbing');
      try{ stage.setPointerCapture(e.pointerId); }catch(_){}
    });
    stage.addEventListener('pointermove', function(e){
      if(!drag) return;
      var r=svg.getBoundingClientRect();
      vb[0]=sox-(e.clientX-sx)*(vb[2]/r.width);
      vb[1]=soy-(e.clientY-sy)*(vb[3]/r.height); set();
    });
    function end(){ drag=false; stage.classList.remove('grabbing'); }
    stage.addEventListener('pointerup', end);
    stage.addEventListener('pointercancel', end);
    stage.addEventListener('dblclick', function(e){
      e.preventDefault(); vb=vb0.slice(); set();
    });
  });
})();
/*]]>*/</script>
"""


# Runs BEFORE the CSS is applied so a returning visitor's saved choice takes
# effect on first paint (no flash of the wrong theme) — reads localStorage
# and stamps ``data-theme`` on <html> immediately; absent a saved choice, no
# attribute is set and the page falls through to the
# ``@media (prefers-color-scheme: dark)`` rule (follow the OS).
_THEME_INIT_SCRIPT = """<script>
(function(){
  try {
    var saved = localStorage.getItem("lv-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {}
})();
</script>"""

# The visible toggle button: flips data-theme between light/dark and persists
# the choice, so BOTH the page chrome (--bg/--panel/--ink/...) and every
# inlined SVG's --lv-* vars (see _theme_style_block) recolor live, in place —
# no re-render, one set of CSS custom properties driving both.
_THEME_TOGGLE_SCRIPT = """
<script>
(function(){
  var btn = document.getElementById("theme-toggle");
  function isDark(){
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function sync(){ btn.textContent = isDark() ? "Light mode" : "Dark mode"; }
  btn.addEventListener("click", function(){
    var next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("lv-theme", next); } catch (e) {}
    sync();
  });
  sync();
})();
</script>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "outputs" / "gallery",
                    help="output directory (default: outputs/gallery)")
    args = ap.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    # The sampler is the ONE opt-in consumer of the var-driven theme (task
    # #26): every embedded SVG gets ``theme.<field>`` values wrapped as
    # ``var(--lv-<role>, <light-hex>)``, so the ``<style>`` block below can
    # recolor them live without a re-render. Every other renderer caller
    # (tests, the docs pipeline, ``render_vi_file`` by default) still gets
    # ``DEFAULT_THEME``'s raw hex — unaffected.
    sampler_theme = css_var_theme()

    cards: list[str] = []
    ok = skipped = failed = 0
    for s in CORPUS:
        vi = REPO_ROOT / s.path
        if not vi.exists():
            print(f"  SKIP  {s.name:22} (missing: {s.path})")
            skipped += 1
            continue
        try:
            svg = render_vi_file(vi, expand_subvis=False, theme=sampler_theme)
        except Exception as e:  # noqa: BLE001 — report, never abort the run
            print(f"  FAIL  {s.name:22} {type(e).__name__}: {e}")
            cards.append(
                f'<figure class="card"><figcaption><b>{html.escape(s.name)}'
                f'</b></figcaption><div class="fail">render failed: '
                f'{html.escape(type(e).__name__)}</div></figure>')
            failed += 1
            continue
        if not svg:
            print(f"  NONE  {s.name:22} (renderer declined — no geometry)")
            failed += 1
            continue
        (out / f"{s.name}.svg").write_text(svg)
        cards.append(_card(s, svg))
        print(f"  OK    {s.name:22} ({len(svg)} bytes)")
        ok += 1

    index = out / "index.html"
    page = _PAGE.format(
        count=ok, cards="\n".join(cards),
        theme_init_script=_THEME_INIT_SCRIPT,
        theme_vars_css=_theme_style_block(),
    )
    index.write_text(page + _SCRIPT + _THEME_TOGGLE_SCRIPT)

    print(f"\n{ok} rendered, {skipped} skipped, {failed} failed")
    print(f"\nOpen: {index.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

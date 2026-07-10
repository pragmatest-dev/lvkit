"""Faithful, graph-driven LabVIEW block-diagram rendering to SVG.

The graph (``InMemoryVIGraph``) is the single source of truth for semantics
(wire connectivity, node kinds, names, types); the heap XML supplies ONLY
geometry the parser otherwise discards. See ``experiments/lv-renderer/DESIGN.md``.

Pipeline: ``render/layout.py`` geometry + graph semantics -> ``scene.py``
(``Scene`` view model, resolving each node's ``Glyph`` via ``nodes.py``'s
resolver chain) -> ``draw.py`` (replays the resolved glyphs/structures/wires)
-> ``backend.py`` (``SvgBackend``) -> SVG string.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..graph.core import InMemoryVIGraph
from .backend import SvgBackend
from .draw import draw_scene
from .scene import Scene, build_scene
from .style import DEFAULT_THEME

__all__ = ["Scene", "build_scene", "render_vi", "render_vi_file"]

# Deterministic per-VI id for the root <svg> — sanitized from the VI name, no
# randomness/time (byte-reproducibility rule), so the inline frame-controller
# script below can scope its DOM queries to exactly this document, and many
# inlined SVGs on one HTML page (the gallery) don't collide with each other.
_ID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9]+")


def _root_id(vi_name: str) -> str:
    return "lv-" + _ID_SANITIZE_RE.sub("-", vi_name).strip("-")


# Interactive frame controller (roadmap #17): a case/stacked-sequence selector
# with ◄/► prev-next arrows AND a ▼ dropdown that opens a real menu of frame
# values to pick from. Selecting a frame shows/hides the matching ``lv-frame``
# groups (content + value-label groups) by ANDing every ``struct=value`` segment
# of their ``data-path`` against current state — which is what makes nesting
# compose. A delegated click handler reads ``data-lv-action`` (prev/next/toggle)
# and ``data-lv-value`` (a menu option) off the clicked target. Scoped to this
# SVG's own root id and guarded against double-init so a page embedding many
# SVGs is safe. No Math.random()/Date.
# Connector-pane hover reveal (roadmap #40 front end): every drawn node is
# wrapped in a ``.lv-node`` group carrying a child ``.lv-help`` group (the
# LabVIEW-Context-Help-style panel — see draw.py::_draw_connector_pane),
# hidden by default. Pure CSS, no JS: `:hover` reveals it. `!important`
# beats the inline `display:none` written on the group itself (its only
# purpose is to hide it from non-hover renders/screenshots).
# `pointer-events:none` on the panel keeps it from stealing hover off the
# node underneath it (it can visually sit over sibling nodes; see draw.py).
_CONNECTOR_PANE_CSS = (
    ".lv-node:hover .lv-help{display:block !important}"
    ".lv-help{pointer-events:none}"
)


_FRAME_CONTROLLER_JS = """(function() {
  var root = document.getElementById(__ROOT_ID__);
  if (!root || root.__lvInit) return;
  root.__lvInit = true;
  var config = {}, state = {};
  var carriers = root.querySelectorAll("[data-lv-frames]");
  for (var i = 0; i < carriers.length; i++) {
    var el = carriers[i], s = el.getAttribute("data-lv-struct");
    config[s] = { frames: el.getAttribute("data-lv-frames").split(";"),
                  def: el.getAttribute("data-lv-default") };
    state[s] = config[s].def;
  }
  function apply() {
    var gs = root.querySelectorAll(".lv-frame");
    for (var i = 0; i < gs.length; i++) {
      var g = gs[i], segs = g.getAttribute("data-path").split(";"), vis = true;
      for (var j = 0; j < segs.length; j++) {
        var seg = segs[j];
        if (!seg) continue;
        var k = seg.indexOf("=");
        if (state[seg.slice(0, k)] !== seg.slice(k + 1)) { vis = false; break; }
      }
      g.style.display = vis ? "" : "none";
    }
  }
  function closeMenus(except) {
    var ms = root.querySelectorAll(".lv-menu");
    for (var i = 0; i < ms.length; i++)
      if (ms[i] !== except) ms[i].style.display = "none";
  }
  root.addEventListener("click", function(e) {
    var t = e.target;
    while (t && t !== root && t.getAttribute &&
           t.getAttribute("data-lv-action") === null &&
           t.getAttribute("data-lv-value") === null) t = t.parentNode;
    if (!t || t === root || !t.getAttribute) { closeMenus(null); return; }
    var s = t.getAttribute("data-lv-struct");
    var val = t.getAttribute("data-lv-value");
    var action = t.getAttribute("data-lv-action");
    if (val !== null) { state[s] = val; apply(); closeMenus(null); }
    else if (action === "prev" || action === "next") {
      var f = config[s].frames, idx = f.indexOf(state[s]);
      state[s] = f[(idx + (action === "next" ? 1 : f.length - 1)) % f.length];
      apply(); closeMenus(null);
    } else if (action === "toggle") {
      var m = root.querySelector('.lv-menu[data-lv-struct="' + s + '"]');
      if (m) {
        var open = m.style.display !== "none";
        closeMenus(m);
        m.style.display = open ? "none" : "";
      }
    }
  });
  apply();
})();"""


def render_vi(graph: InMemoryVIGraph, vi_name: str) -> str | None:
    """Render one VI's block diagram to an SVG string.

    Returns None (fail-closed) if required geometry is missing — the graph
    knows the VI exists and what it contains, but the heap XML doesn't have
    a diagram position for something semantically required. Callers (e.g.
    the docs pipeline) should fall back to a non-geometric rendering.

    The SVG is self-contained and interactive: a case structure's
    ``◄ value ▼ ►`` selector is clickable and cycles through its frames (see
    ``_FRAME_CONTROLLER_JS``); non-JS consumers still see the default frame.
    """
    scene = build_scene(graph, vi_name)
    if scene is None:
        return None
    backend = SvgBackend()
    draw_scene(scene, backend, DEFAULT_THEME)
    # Only VIs with interactive case structures carry the root id + inline
    # frame-controller script; a diagram with no case structures (loops, flat/
    # stacked sequences, straight-line dataflow) renders byte-identically to
    # the pre-#17 output — no dead JS shipped in every SVG.
    if scene.frame_values:
        root_id = _root_id(vi_name)
        script = _FRAME_CONTROLLER_JS.replace("__ROOT_ID__", json.dumps(root_id))
        return backend.render(
            scene.bounds, title=vi_name, script=script, root_id=root_id,
            style=_CONNECTOR_PANE_CSS,
        )
    return backend.render(scene.bounds, title=vi_name, style=_CONNECTOR_PANE_CSS)


def render_vi_file(
    path: Path,
    *,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    expand_subvis: bool = True,
) -> str | None:
    """Render a ``.vi`` file (or ``_BDHb.xml`` heap) straight from disk,
    building a fresh graph.

    ``expand_subvis`` defaults to True so SubVI calls resolve to their real
    ``.vi`` files and their extracted icons appear on the diagram; pass the
    library roots / search paths so vi.lib / user.lib SubVIs resolve. If
    expansion fails (unresolvable deps), it degrades to a diagram-only load so
    the VI still renders (fallback boxes for unresolved SubVIs)."""
    path = Path(path)
    vi_name_hint = (
        path.name.replace("_BDHb.xml", ".vi")
        if path.name.endswith("_BDHb.xml")
        else path.name
    )

    def _load(expand: bool) -> InMemoryVIGraph:
        graph = InMemoryVIGraph()
        if vilib_root or userlib_root:
            graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)
        graph.load_vi(path, expand_subvis=expand, search_paths=search_paths)
        return graph

    try:
        graph = _load(expand_subvis)
    except Exception:
        if not expand_subvis:
            raise
        graph = _load(False)  # degrade: still render this VI's own diagram
    return render_vi(graph, graph.resolve_vi_name(vi_name_hint))

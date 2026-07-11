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
from ..graph.models import VINode
from .backend import SvgBackend
from .draw import draw_scene
from .scene import Scene, build_scene
from .style import DEFAULT_THEME

__all__ = [
    "Scene",
    "build_scene",
    "render_vi",
    "render_vi_file",
    "render_vi_with_subvis",
]

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


# Connector-panel hover controller (roadmap #40 rework): panels are no longer
# children of their node (see draw.py::draw_help_overlay — they're all in one
# overlay group at the END of the document, so they paint over everything
# else), so plain CSS ``:hover`` can no longer reveal them — this JS drives
# show/hide AND positions each panel at runtime.
#
# Each ``.lv-node`` carries ``data-node`` matching its ``.lv-help`` panel's
# own ``data-node`` (built into a lookup map once, at init — no dynamic
# attribute-selector string-building, so odd characters in a node id can't
# break a selector). On ``mouseenter`` the panel is measured with
# ``getBBox()`` (it starts ``visibility:hidden``, not ``display:none``, so it
# still has real geometry to measure — see draw.py), placed OUTSIDE the
# hovered node's own box (above/below/left/right, in that preference order —
# the panel must never cover the very node it's explaining, since that would
# hide the wires the user is trying to identify), then clamped fully inside
# the root SVG's ``viewBox`` (sliding left/up if it would overflow right/
# bottom, right/down if it would overflow left/top). If every side still
# overlaps the node after clamping (a tight corner smaller than the panel),
# it's pushed further off the node along whichever axis has room, so the
# node's own rect stays maximally clear even though the viewBox can't fit
# the panel entirely clear of it. On ``mouseleave`` the panel hides again.
_HOVER_PANEL_JS = """(function() {
  var root = document.getElementById(__ROOT_ID__);
  if (!root || root.__lvHoverInit) return;
  root.__lvHoverInit = true;
  var GAP = 8;
  var helpByNode = {};
  var helps = root.querySelectorAll(".lv-help");
  for (var i = 0; i < helps.length; i++) {
    helpByNode[helps[i].getAttribute("data-node")] = helps[i];
  }
  function viewBox() {
    var parts = root.getAttribute("viewBox").split(/\\s+/);
    return [parseFloat(parts[0]), parseFloat(parts[1]),
            parseFloat(parts[2]), parseFloat(parts[3])];
  }
  function clamp(x, y, w, h, vb) {
    var minX = vb[0], minY = vb[1], maxX = vb[0] + vb[2], maxY = vb[1] + vb[3];
    if (x + w > maxX) x = maxX - w;
    if (y + h > maxY) y = maxY - h;
    if (x < minX) x = minX;
    if (y < minY) y = minY;
    return [x, y];
  }
  function overlapsNode(x, y, w, h, nb) {
    return x < nb.x + nb.width && x + w > nb.x &&
           y < nb.y + nb.height && y + h > nb.y;
  }
  function place(nodeEl, help) {
    var nb = nodeEl.getBBox();
    var hb = help.getBBox();
    var vb = viewBox();
    var candidates = [
      [nb.x, nb.y - hb.height - GAP],               // above, left-aligned
      [nb.x, nb.y + nb.height + GAP],                // below, left-aligned
      [nb.x + nb.width + GAP, nb.y],                 // right, top-aligned
      [nb.x - hb.width - GAP, nb.y],                 // left, top-aligned
    ];
    var chosen = null;
    for (var i = 0; i < candidates.length; i++) {
      var p = clamp(candidates[i][0], candidates[i][1], hb.width, hb.height, vb);
      if (!overlapsNode(p[0], p[1], hb.width, hb.height, nb)) { chosen = p; break; }
    }
    if (!chosen) {
      // Tight corner: every side still overlaps the node after clamping.
      // Push as far off the node as the viewBox allows -- above first, else
      // below, else left, else right -- so the node's own rect stays as
      // clear as possible even though a perfect fit isn't available.
      var x = nb.x, y = nb.y - hb.height - GAP;
      if (y < vb[1]) {
        y = nb.y + nb.height + GAP;
        if (y + hb.height > vb[1] + vb[3]) {
          x = nb.x - hb.width - GAP;
          y = nb.y;
          if (x < vb[0]) { x = nb.x + nb.width + GAP; }
        }
      }
      chosen = clamp(x, y, hb.width, hb.height, vb);
    }
    help.setAttribute("transform", "translate(" + chosen[0] + "," + chosen[1] + ")");
  }
  var nodes = root.querySelectorAll(".lv-node");
  for (var i = 0; i < nodes.length; i++) {
    var nodeEl = nodes[i];
    var help = helpByNode[nodeEl.getAttribute("data-node")];
    if (!help) continue;
    (function(nodeEl, help) {
      nodeEl.addEventListener("mouseenter", function() {
        place(nodeEl, help);
        help.style.visibility = "visible";
      });
      nodeEl.addEventListener("mouseleave", function() {
        help.style.visibility = "hidden";
      });
    })(nodeEl, help);
  }
})();"""


def render_vi(graph: InMemoryVIGraph, vi_name: str) -> str | None:
    """Render one VI's block diagram to an SVG string.

    Returns None (fail-closed) if required geometry is missing — the graph
    knows the VI exists and what it contains, but the heap XML doesn't have
    a diagram position for something semantically required. Callers (e.g.
    the docs pipeline) should fall back to a non-geometric rendering.

    The SVG is self-contained and interactive: a case structure's
    ``◄ value ▼ ►`` selector is clickable and cycles through its frames (see
    ``_FRAME_CONTROLLER_JS``), and hovering a node reveals its connector-help
    panel, positioned clear of the node and clamped inside the viewBox (see
    ``_HOVER_PANEL_JS``); non-JS consumers still see the default frame and no
    panels (both degrade gracefully — the SVG is static without JS).
    """
    scene = build_scene(graph, vi_name)
    if scene is None:
        return None
    return _render_scene_svg(scene, vi_name)


def _render_scene_svg(scene: Scene, vi_name: str) -> str:
    """Draw an already-built ``Scene`` to a self-contained interactive SVG."""
    backend = SvgBackend()
    draw_scene(scene, backend, DEFAULT_THEME)
    # Only a VI that actually needs JS (interactive case/sequence frames,
    # and/or at least one connector-help panel) carries the root id + inline
    # script — a diagram with neither renders byte-identically to a version
    # with no interactivity at all, no dead JS shipped in every SVG.
    scripts = []
    if scene.frame_values:
        scripts.append(_FRAME_CONTROLLER_JS)
    if scene.nodes:
        scripts.append(_HOVER_PANEL_JS)
    if scripts:
        root_id = _root_id(vi_name)
        script = "\n".join(scripts).replace("__ROOT_ID__", json.dumps(root_id))
        return backend.render(
            scene.bounds, title=vi_name, script=script, root_id=root_id,
        )
    return backend.render(scene.bounds, title=vi_name)


def render_vi_with_subvis(
    graph: InMemoryVIGraph, vi_name: str,
) -> tuple[str | None, dict[str, str]]:
    """Render one VI, and also return the ``data-node`` id -> target VI name map
    for the subVI-call nodes on its diagram.

    The renderer stays navigation-free; this just surfaces WHICH drawn nodes are
    subVIs (structure the graph already knows) so a consumer like the docs
    pipeline can attach its own click-to-navigate behavior to those nodes by
    their ``data-node`` id. Returns ``(None, {})`` when the VI has no drawable
    geometry (same fail-closed contract as ``render_vi``)."""
    scene = build_scene(graph, vi_name)
    if scene is None:
        return None, {}
    # Prefer the fully-qualified callee name (e.g. "TestCase.lvclass:run.vi")
    # so a consumer can identify the exact VI; fall back to the bare name.
    subvis: dict[str, str] = {}
    for rn in scene.nodes:
        if isinstance(rn.node, VINode):
            target = rn.node.qualified_name or rn.node.name
            if target:
                subvis[rn.node.id] = target
    return _render_scene_svg(scene, vi_name), subvis


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

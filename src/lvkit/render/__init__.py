"""Faithful, graph-driven LabVIEW block-diagram rendering to SVG.

The graph (``InMemoryVIGraph``) is the single source of truth for semantics
(wire connectivity, node kinds, names, types); the heap XML supplies ONLY
geometry the parser otherwise discards. See ``experiments/lv-renderer/DESIGN.md``.

Pipeline: ``parser/layout.py`` geometry + graph semantics -> ``scene.py``
(``Scene`` view model, resolving each node's ``Glyph`` via ``nodes.py``'s
resolver chain) -> ``composite.py`` (builds ONE hierarchical composite and
draws it with a single recursive ``root.draw()`` — nesting by graph containment,
paint order by the layout's zPlaneList rank, opaque structure bodies, reusing
the leaf pixel helpers in ``draw.py`` and the structure body glyphs in
``glyphs/structures/``) -> ``backend.py`` (``SvgBackend``) -> SVG string.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from ..graph.core import InMemoryVIGraph
from ..graph.loading import LoadMode
from ..graph.models import VINode, vi_health_to_dict, vi_properties_to_dict
from .backend import SvgBackend
from .composite import draw_scene
from .connector_pane import pane_terminals, render_connector_pane_help
from .icons import icon_data_uri
from .scene import Scene, build_scene
from .style import DEFAULT_THEME, Theme, css_var_theme
from .theme_web import embedded_dark_css

# How a rendered SVG carries light/dark colors:
#   "light" — raw-hex ``DEFAULT_THEME``, self-contained, UNCHANGED legacy output.
#   "dark"  — css-var theme + an embedded ``:root`` block forcing the dark
#             ``--lv-*`` palette (a standalone .svg opens dark).
#   "auto"  — css-var theme + the dark palette wrapped in
#             ``@media (prefers-color-scheme: dark)`` (light by default, dark
#             when the host/OS/editor prefers dark).
ThemeMode = Literal["light", "dark", "auto"]

__all__ = [
    "Scene",
    "build_scene",
    "render_vi",
    "render_vi_file",
    "render_vi_file_titled",
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
      g.classList.toggle("lv-frame-hidden", !vis);
    }
  }
  function closeMenus(except) {
    var ms = root.querySelectorAll(".lv-menu");
    for (var i = 0; i < ms.length; i++)
      if (ms[i] !== except) ms[i].classList.remove("lv-selector-open");
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
        var open = m.classList.contains("lv-selector-open");
        closeMenus(m);
        m.classList.toggle("lv-selector-open", !open);
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
    // This node has a visual connector panel, so its native <title> tooltip
    // would double up with the panel (a text hover AND a rendered box). Move
    // the title text to aria-label (keeps the accessible name for screen
    // readers) and drop the <title>, so JS users see ONLY the panel. Nodes
    // without a panel (constants) keep their <title> -- it's their only hover.
    var titleEl = nodeEl.querySelector("title");
    if (titleEl) {
      nodeEl.setAttribute("aria-label", titleEl.textContent);
      nodeEl.removeChild(titleEl);
    }
    (function(nodeEl, help) {
      nodeEl.addEventListener("mouseenter", function() {
        // A viewer host sets root.__lvSuppressPanel and instead clones this same
        // <g class="lv-help"> into a fixed, viewport-clamped HTML overlay (the
        // scroll lives in the host, so it must own placement). A raw standalone
        // .svg leaves the flag unset and reveals the panel in place -- its only
        // hover affordance.
        if (root.__lvSuppressPanel) return;
        place(nodeEl, help);
        help.classList.add("lv-help-shown");
      });
      nodeEl.addEventListener("mouseleave", function() {
        help.classList.remove("lv-help-shown");
      });
    })(nodeEl, help);
  }
})();"""


def _resolve_theme_mode(
    theme_mode: ThemeMode,
    base: Theme,
) -> tuple[Theme, str]:
    """Map a ``theme_mode`` to the ``(theme, extra_css)`` a render needs.

    ``"light"`` keeps ``base`` and injects nothing (byte-identical legacy
    path). ``"dark"``/``"auto"`` swap to a css-var theme (so ``--lv-*`` drive
    the colors) and return the CSS that defines the dark palette — applied
    unconditionally for ``"dark"``, media-queried for ``"auto"`` (see
    ``theme_web.embedded_dark_css``)."""
    if theme_mode == "light":
        return base, ""
    return css_var_theme(base), embedded_dark_css(theme_mode)


def _vi_properties_data_attrs(
    graph: InMemoryVIGraph,
    vi_name: str,
) -> dict[str, str]:
    """Compact-JSON ``data-lv-properties``/``data-lv-health`` payloads for
    this VI's ``VIProperties``/``VIHealth`` facets (task #19).

    The root ``<svg>`` is the single carrier for VI properties + health —
    read alike by the render-viewer chrome (status chips + the collapsible
    Properties panel, see ``render_viewer.py``) and any other host that only
    ever sees the raw SVG (e.g. the VS Code extension), same pattern as the
    existing ``data-lv-vi-rel`` per-node attribute. ``get_vi_context`` is the
    documented way to reach these graph facets (``VIContext.properties`` /
    ``.health``); a missing/unloaded VI degrades to the all-defaults
    ``VIContext`` it already returns, never raises.

    ``vi_properties_to_dict``/``vi_health_to_dict`` (``graph/models.py``)
    are the canonical converters -- ``LockState`` (an ``Enum``) swapped for
    its ``.value`` string, ``VIHealth.is_broken`` (a derived ``@property``,
    so ``dataclasses.asdict`` skips it) added explicitly -- shared with the
    netlist JSON IR (``netlist.py``'s ``netlist_to_dict``) so both surfaces
    stay byte-for-byte the same shape.
    """
    ctx = graph.get_vi_context(vi_name)
    props = vi_properties_to_dict(ctx.properties)
    health = vi_health_to_dict(ctx.health)
    return {
        "lv-properties": json.dumps(props, separators=(",", ":"), sort_keys=True),
        "lv-health": json.dumps(health, separators=(",", ":"), sort_keys=True),
    }


def render_vi(
    graph: InMemoryVIGraph,
    vi_name: str,
    *,
    theme: Theme = DEFAULT_THEME,
    interactive: bool = True,
    theme_mode: ThemeMode = "light",
) -> str | None:
    """Render one VI's block diagram to an SVG string.

    Returns None (fail-closed) if required geometry is missing — the graph
    knows the VI exists and what it contains, but the heap XML doesn't have
    a diagram position for something semantically required. Callers (e.g.
    the docs pipeline) should fall back to a non-geometric rendering.

    ``theme`` defaults to ``DEFAULT_THEME`` (raw hex, self-contained SVG).
    Pass ``style.css_var_theme()`` for a page (e.g. the sampler) that embeds
    the SVG inline and wants its colors driven live by page CSS custom
    properties instead.

    ``theme_mode`` selects the self-contained light/dark behavior of the
    returned SVG (see :data:`ThemeMode`): ``"light"`` (default) is the
    UNCHANGED raw-hex output; ``"dark"``/``"auto"`` render with a css-var theme
    and embed the dark ``--lv-*`` palette so the file is dark unconditionally
    (``"dark"``) or follows ``prefers-color-scheme`` (``"auto"``). ``"light"``
    ignores any embedded palette and is byte-identical to before this flag.

    The SVG is self-contained and interactive: a case structure's
    ``◄ value ▼ ►`` selector is clickable and cycles through its frames (see
    ``_FRAME_CONTROLLER_JS``), and hovering a node reveals its connector-help
    panel, positioned clear of the node and clamped inside the viewBox (see
    ``_HOVER_PANEL_JS``); non-JS consumers still see the default frame and no
    panels (both degrade gracefully — the SVG is static without JS).

    ``interactive=False`` (default ``True``) skips BOTH inline scripts and the
    root ``<svg id=...>`` they need — no ``<script>``, no root id, at all. The
    ``data-node``/``data-lv-struct``/``data-lv-frames``/``data-lv-default``/
    ``data-path`` attributes are drawn regardless (they come from ``draw.py``
    scene-drawing, not the scripts), so a consumer that drives its own frame
    behavior (e.g. the multi-SVG diff viewer, which needs two SVGs on
    one page with no id collision and no dueling JS) still has everything it
    needs to query. Default (``True``) output is byte-identical to before this
    flag existed.
    """
    scene = build_scene(graph, vi_name)
    if scene is None:
        return None
    theme, extra_css = _resolve_theme_mode(theme_mode, theme)
    extra_attrs = _vi_properties_data_attrs(graph, vi_name)
    svg = _render_scene_svg(
        scene,
        vi_name,
        theme,
        interactive=interactive,
        extra_css=extra_css,
        extra_attrs=extra_attrs,
        display_name=graph.vi_display_name(vi_name),
    )
    aside = _vi_aside_svg(graph, vi_name, scene, theme)
    if aside:
        cut = svg.rfind("</svg>")
        svg = svg[:cut] + aside + svg[cut:]
    return svg


_ASIDE_PAD = 8.0


def _vi_aside_svg(
    graph: InMemoryVIGraph,
    vi_name: str,
    scene: Scene,
    theme: Theme,
) -> str:
    """The VI's connector-pane Context-Help panel (icon + title + description +
    the pane grid with each terminal's name on a leader stub), pinned TOP-RIGHT
    like a real VI's corner. Wrapped in ``<defs>`` so it is STRUCTURALLY
    non-rendered — never painted by any conformant renderer regardless of CSS/JS
    (survives JS-strip AND SVG sanitization, unlike inline display:none). A raw
    SVG (GitHub, email, a rasterizer) never leaks it; only a host with JS (the
    render/diff viewer) reveals it, by CLONING this group out of ``<defs>`` into
    the visible tree — the transform pins the clone top-right. Returns "" when
    the VI has no connector pane."""
    ctx = graph.get_vi_context(vi_name)
    if ctx.connector_pattern_id is None:
        return ""
    icon_uri = icon_data_uri(scene.icon_png) if scene.icon_png is not None else None
    panel = render_connector_pane_help(
        ctx.connector_pattern_id,
        pane_terminals((*ctx.inputs, *ctx.outputs)),
        title=graph.vi_display_name(vi_name).split(":")[-1],
        description=ctx.description,
        icon_uri=icon_uri,
        theme=theme,
    )
    m = re.search(r'width="([\d.]+)"', panel)
    panel_w = float(m.group(1)) if m else 240.0
    _, y1, x2, _ = scene.bounds
    tx = x2 - panel_w - _ASIDE_PAD
    ty = y1 + _ASIDE_PAD
    return (
        f'<defs><g class="lv-vi-aside" '
        f'transform="translate({tx:.1f},{ty:.1f})">{panel}</g></defs>'
    )


# Base CSS emitted with every rendered SVG: SVG <text> defaults to the text
# (I-beam) cursor because it's selectable — a diagram should read as a normal
# pointer surface, so force the default cursor and make the text un-selectable
# (dragging/panning over labels shouldn't start a text selection).
_BASE_CSS = (
    "text{cursor:default;-webkit-user-select:none;user-select:none}"
    # Static presentation moved off inline style= attributes onto classes, and
    # the JS below flips STATE by swapping a class (never by writing .style):
    #   .lv-clickable    interactive selector arrows / value box / menu rows
    #   .lv-frame.lv-frame-hidden  a case/sequence frame not currently selected
    #   .lv-menu / .lv-selector-open  the frame dropdown, closed / open
    #   .lv-disabled-mask    the translucent wash over a disabled subdiagram
    #   .lv-raster           pixel-art icons scaled up crisp, not smoothed
    ".lv-clickable{cursor:pointer}"
    ".lv-frame.lv-frame-hidden{display:none}"
    ".lv-label{pointer-events:none}"
    ".lv-menu{display:none}.lv-menu.lv-selector-open{display:inline}"
    ".lv-disabled-mask{pointer-events:none;opacity:.5}"
    ".lv-raster{image-rendering:pixelated}"
    # The connector-pane hover panel: hidden by default, revealed on hover via
    # the .lv-help-shown class (added in place by the script below, or on the clone
    # in a host overlay -- an SVG <style> is document-global, so this ONE rule
    # styles both). Soft drop shadow lifts it off the diagram (matches the
    # viewer tooltip + the minimap).
    ".lv-help{visibility:hidden;pointer-events:none;"
    "filter:drop-shadow(0 2px 12px rgba(0,0,0,.28))}"
    ".lv-help.lv-help-shown{visibility:visible}"
    # The VI's icon + connector-pane aside (top-right, like a real VI): parked
    # in <defs> so it is STRUCTURALLY non-rendered by default (rasterizers and
    # sanitizers never paint it — see _vi_aside_svg); a host (render/diff viewer)
    # reveals it by lifting a clone OUT of <defs> into the visible tree. This
    # rule only adds the lift shadow once shown. Inside the viewBox, so it never
    # grows the canvas.
    ".lv-vi-aside{filter:drop-shadow(0 2px 12px rgba(0,0,0,.28))}"
)


def _render_scene_svg(
    scene: Scene,
    vi_name: str,
    theme: Theme = DEFAULT_THEME,
    *,
    interactive: bool = True,
    extra_css: str = "",
    extra_attrs: dict[str, str] | None = None,
    display_name: str | None = None,
) -> str:
    """Draw an already-built ``Scene`` to a self-contained interactive SVG.

    ``extra_css`` (empty by default) is appended to the SVG's ``<style>`` — the
    dark ``--lv-*`` palette block for ``dark``/``auto`` theme modes. Empty keeps
    the ``<style>`` byte-identical to the legacy light output. ``extra_attrs``
    (``None`` by default) becomes root-``<svg>`` ``data-*`` attributes — e.g.
    ``render_vi``'s ``data-lv-properties``/``data-lv-health`` (task #19).
    ``display_name`` (defaults to ``vi_name``) is the DISPLAY-only accessible
    name (aria-label): the qualified name, so the SVG never exposes the abspath
    ``vi_name`` key. ``vi_name`` still drives the scoping ``root_id``."""
    title = display_name if display_name is not None else vi_name
    backend = SvgBackend()
    draw_scene(scene, backend, theme)
    style = _BASE_CSS + extra_css
    # Only a VI that actually needs JS (interactive case/sequence frames,
    # and/or at least one connector-help panel) carries the root id + inline
    # script — a diagram with neither renders byte-identically to a version
    # with no interactivity at all, no dead JS shipped in every SVG.
    # ``interactive=False`` forces an empty list — no scripts, no root id —
    # for a consumer (e.g. the diff viewer) that drives its own frame/hover
    # behavior over the surviving ``data-*`` attributes instead.
    scripts = []
    if interactive:
        if scene.frame_values:
            scripts.append(_FRAME_CONTROLLER_JS)
        if scene.nodes:
            scripts.append(_HOVER_PANEL_JS)
    if scripts:
        root_id = _root_id(vi_name)
        script = "\n".join(scripts).replace("__ROOT_ID__", json.dumps(root_id))
        return backend.render(
            scene.bounds,
            title=title,
            script=script,
            root_id=root_id,
            style=style,
            extra_attrs=extra_attrs,
        )
    return backend.render(
        scene.bounds,
        title=title,
        style=style,
        extra_attrs=extra_attrs,
    )


def render_vi_with_subvis(
    graph: InMemoryVIGraph,
    vi_name: str,
    *,
    theme: Theme = DEFAULT_THEME,
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
                # Key by the DOCUMENT-LOCAL dom_id — the same value emitted as
                # ``data-node`` — so the docs NAV map matches the SVG (and neither
                # carries the abspath vi_key).
                subvis[rn.dom_id] = target
    return _render_scene_svg(scene, vi_name, theme), subvis


def render_vi_file(
    path: Path,
    *,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    mode: LoadMode = LoadMode.MINIMAL,
    theme: Theme = DEFAULT_THEME,
    theme_mode: ThemeMode = "light",
) -> str | None:
    """Render a ``.vi`` file (or ``_BDHb.xml`` heap) straight from disk,
    building a fresh graph.

    ``theme_mode`` is forwarded to :func:`render_vi` — ``"light"`` (default,
    unchanged raw-hex output), ``"dark"``, or ``"auto"`` (follows the viewer's
    ``prefers-color-scheme``).

    ``mode`` defaults to ``LoadMode.MINIMAL`` — the target VI plus its direct
    SubVIs' connector panes and referenced-type fields, which renders
    byte-identically to a full load but never walks the transitive SubVI tree
    (8-40x faster on a deep hierarchy). Pass the library roots / search paths so
    vi.lib / user.lib SubVIs resolve. If the load fails (unresolvable deps), it
    degrades to ``LoadMode.NONE`` (this VI's own diagram only, fallback boxes for
    SubVIs) so the VI still renders."""
    return render_vi_file_titled(
        path,
        search_paths=search_paths,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
        mode=mode,
        theme=theme,
        theme_mode=theme_mode,
    )[0]


def render_vi_file_titled(
    path: Path,
    *,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    mode: LoadMode = LoadMode.MINIMAL,
    theme: Theme = DEFAULT_THEME,
    theme_mode: ThemeMode = "light",
) -> tuple[str | None, str]:
    """Like :func:`render_vi_file`, but also returns the VI's resolved
    (fully qualified, e.g. ``Class.lvclass:vi.vi``) name — for use as a viewer
    title. Same load/degrade behaviour."""
    path = Path(path)
    vi_name_hint = (
        path.name.replace("_BDHb.xml", ".vi")
        if path.name.endswith("_BDHb.xml")
        else path.name
    )

    def _load(load_mode: LoadMode) -> InMemoryVIGraph:
        graph = InMemoryVIGraph()
        if vilib_root or userlib_root:
            graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)
        graph.load_vi(path, mode=load_mode, search_paths=search_paths, layout=True)
        return graph

    try:
        graph = _load(mode)
    except Exception:
        if mode is LoadMode.NONE:
            raise
        graph = _load(LoadMode.NONE)  # degrade: still render this VI's own diagram
    name = graph.resolve_vi_name(vi_name_hint)
    # Every command that parses a VI warms the index — a render parses this VI
    # (and, under MINIMAL, its SubVIs) into `graph`, so upsert their facts
    # (best-effort; never fails the render). A cache HIT never reaches here, so
    # a warm render pays nothing extra.
    from ..index.build import warm_all_loaded

    warm_all_loaded(graph)
    svg = render_vi(graph, name, theme=theme, theme_mode=theme_mode)
    # `name` is the vi_key (canonical source path) — the render identity. The
    # TITLE is display-only: the qualified name, never the abspath key.
    return svg, graph.vi_display_name(name)

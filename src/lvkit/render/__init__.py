"""Faithful, graph-driven LabVIEW block-diagram rendering to SVG.

The graph (``InMemoryVIGraph``) is the single source of truth for semantics
(wire connectivity, node kinds, names, types); the heap XML supplies ONLY
geometry the parser otherwise discards. See ``experiments/lv-renderer/DESIGN.md``.

Pipeline: ``graph/layout.py`` geometry + graph semantics -> ``scene.py``
(``Scene`` view model) -> ``draw.py`` (dispatch-dict drawer) -> ``backend.py``
(``SvgBackend``) -> SVG string.
"""

from __future__ import annotations

from pathlib import Path

from ..graph.core import InMemoryVIGraph
from .backend import SvgBackend
from .draw import draw_scene
from .scene import Scene, build_scene
from .style import DEFAULT_THEME

__all__ = ["Scene", "build_scene", "render_vi", "render_vi_file"]


def render_vi(graph: InMemoryVIGraph, vi_name: str) -> str | None:
    """Render one VI's block diagram to an SVG string.

    Returns None (fail-closed) if required geometry is missing — the graph
    knows the VI exists and what it contains, but the heap XML doesn't have
    a diagram position for something semantically required. Callers (e.g.
    the docs pipeline) should fall back to a non-geometric rendering.
    """
    scene = build_scene(graph, vi_name)
    if scene is None:
        return None
    backend = SvgBackend()
    draw_scene(scene, backend, DEFAULT_THEME)
    return backend.render(scene.bounds, title=vi_name)


def render_vi_file(path: Path) -> str | None:
    """Render a ``.vi`` file (or ``_BDHb.xml`` heap) straight from disk,
    building a fresh graph — no SubVI expansion needed since only this VI's
    own diagram is drawn."""
    path = Path(path)
    graph = InMemoryVIGraph()
    graph.load_vi(path, expand_subvis=False)
    vi_name = (
        path.name.replace("_BDHb.xml", ".vi")
        if path.name.endswith("_BDHb.xml")
        else path.name
    )
    vi_name = graph.resolve_vi_name(vi_name)
    return render_vi(graph, vi_name)

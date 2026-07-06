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

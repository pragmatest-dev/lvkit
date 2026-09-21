"""Faithful front-panel rendering: a new VIEW of the graph (per
``ARCHITECTURE.md``'s invariant — every view is a projection of the SAME
graph), a peer of the existing block-diagram renderer (``render/scene.py`` /
``render/__init__.py``), not a branch inside it.

Entry points: ``render_vi_front_panel(graph, vi_name)`` for a VI's own panel,
``render_ctl_front_panel(graph, ctl_key)`` for a standalone ``.ctl`` typedef's
panel. Both read ``ParsedFrontPanel`` straight off the graph node
(``VINode.front_panel`` / the typedef dep-graph node's ``front_panel``
attribute) — never re-parsing the heap themselves, matching how every other
view in this codebase only ever reads the graph.
"""

from __future__ import annotations

from ...parser.models import ParsedFrontPanel
from .. import _BASE_CSS
from ..backend import SvgBackend
from ..style import DEFAULT_THEME, Theme
from .compose import draw_front_panel
from .geometry import build_boxes, content_bounds


def render_front_panel_svg(
    front_panel: ParsedFrontPanel,
    *,
    title: str | None = None,
    theme: Theme = DEFAULT_THEME,
) -> str:
    """Render one already-parsed ``ParsedFrontPanel`` to a self-contained SVG
    string. The shared entry point both ``render_vi_front_panel`` and
    ``render_ctl_front_panel`` reduce to, once each has found its
    ``ParsedFrontPanel`` on the graph.

    Emits the SAME base ``<style>`` (``_BASE_CSS``) the block-diagram
    renderer emits -- reusing ``ArrayConstantGlyph``'s ``lv-disabled-mask``
    wash (an empty array's "unset" rows) only draws right when
    ``.lv-disabled-mask{opacity:.5}`` is actually present; without it the
    mask is a fully OPAQUE rect, hiding the real content it's meant to dim.
    The other rules (``.lv-clickable`` cursor, the frame/menu classes) are
    harmless no-ops here -- nothing in a front-panel SVG has those classes
    except the array index spinner's own click targets, which stay inert
    (no controller JS) exactly per ``ArrayConstantGlyph``'s own documented
    static-fallback contract."""
    boxes = build_boxes(front_panel)
    bounds = content_bounds(boxes)
    backend = SvgBackend()
    backend.rect(*bounds, fill=theme.canvas)
    draw_front_panel(boxes, backend, theme)
    return backend.render(bounds, title=title, style=_BASE_CSS)


def render_vi_front_panel(
    graph: object, vi_name: str, *, theme: Theme = DEFAULT_THEME
) -> str | None:
    """Render the VI ``vi_name``'s OWN front panel (every control, not just
    the connector-pane subset) to a self-contained SVG string. None when the
    VI's graph node carries no front panel (shouldn't happen for a real VI,
    but mirrors ``render_vi``'s fail-closed contract for missing geometry)."""
    from ...graph.models import VINode  # local: avoid a render<->graph cycle

    node = graph._graph.nodes[vi_name]["node"]  # type: ignore[attr-defined]
    if not isinstance(node, VINode) or node.front_panel is None:
        return None
    return render_front_panel_svg(
        node.front_panel, title=node.qualified_name, theme=theme
    )


def render_ctl_front_panel(
    graph: object, ctl_key: str, *, theme: Theme = DEFAULT_THEME
) -> str | None:
    """Render the standalone ``.ctl`` typedef at graph key ``ctl_key``'s OWN
    front panel to a self-contained SVG string. None when the typedef's
    dep-graph node carries no front panel."""
    node = graph._dep_graph.nodes[ctl_key]  # type: ignore[attr-defined]
    fp = node.get("front_panel")
    if fp is None:
        return None
    return render_front_panel_svg(fp, title=node.get("name"), theme=theme)

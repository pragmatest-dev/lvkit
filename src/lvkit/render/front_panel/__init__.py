"""Faithful front-panel rendering: a new VIEW of the graph (per
``ARCHITECTURE.md``'s invariant — every view is a projection of the SAME
graph), a peer of the existing block-diagram renderer (``render/scene.py`` /
``render/__init__.py``), not a branch inside it.

Entry points: ``render_vi_front_panel(graph, vi_name)`` for a VI's own panel,
``render_ctl_front_panel(graph, ctl_key)`` for a standalone ``.ctl`` typedef's
panel. Both read ``ParsedFrontPanel`` through the graph
(``get_vi_front_panel`` / ``get_typedef_front_panel``) — never re-parsing the
heap themselves, matching how every other view in this codebase only ever
reads the graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...parser.models import ParsedFrontPanel
from .. import _BASE_CSS, ThemeMode, resolve_theme_mode
from ..backend import SvgBackend
from ..style import DEFAULT_THEME, Theme
from .compose import draw_front_panel
from .geometry import build_boxes, content_bounds

if TYPE_CHECKING:
    from ...graph.core import InMemoryVIGraph


def render_front_panel_svg(
    front_panel: ParsedFrontPanel,
    *,
    title: str | None = None,
    theme: Theme = DEFAULT_THEME,
    theme_mode: ThemeMode = "light",
) -> str:
    """Render one already-parsed ``ParsedFrontPanel`` to a self-contained SVG
    string. The shared entry point both ``render_vi_front_panel`` and
    ``render_ctl_front_panel`` reduce to, once each has found its
    ``ParsedFrontPanel`` on the graph.

    ``theme_mode`` selects light / dark / auto exactly as the block-diagram
    renderer does (``resolve_theme_mode``): ``"dark"``/``"auto"`` draw with a
    css-var theme and embed the dark ``--lv-*`` palette. A color the panel itself
    records (a frame's fill / outline) is drawn as recorded in every mode.

    Emits the SAME base ``<style>`` (``_BASE_CSS``) the block-diagram
    renderer emits -- the array element column's ``lv-disabled-mask``
    wash (an empty array's "unset" rows) only draws right when
    ``.lv-disabled-mask{opacity:.5}`` is actually present; without it the
    mask is a fully OPAQUE rect, hiding the real content it's meant to dim.
    The other rules (``.lv-clickable`` cursor, the frame/menu classes) are
    harmless no-ops here -- nothing in a front-panel SVG has those classes
    except the array index spinner's own click targets, which stay inert
    (no controller JS) exactly per the array glyphs' documented
    static-fallback contract."""
    theme, extra_css = resolve_theme_mode(theme_mode, theme)
    boxes = build_boxes(front_panel)
    bounds = content_bounds(boxes)
    backend = SvgBackend()
    backend.rect(*bounds, fill=theme.canvas)
    draw_front_panel(boxes, backend, theme)
    return backend.render(bounds, title=title, style=_BASE_CSS + extra_css)


def render_vi_front_panel(
    graph: InMemoryVIGraph,
    vi_name: str,
    *,
    theme: Theme = DEFAULT_THEME,
    theme_mode: ThemeMode = "light",
) -> str | None:
    """Render the VI ``vi_name``'s OWN front panel (every control, not just
    the connector-pane subset) to a self-contained SVG string. None when the
    VI's graph node carries no front panel (shouldn't happen for a real VI,
    but mirrors ``render_vi``'s fail-closed contract for missing geometry)."""
    front_panel = graph.get_vi_front_panel(vi_name)
    if front_panel is None:
        return None
    return render_front_panel_svg(
        front_panel,
        title=graph.vi_display_name(vi_name),
        theme=theme,
        theme_mode=theme_mode,
    )


def render_ctl_front_panel(
    graph: InMemoryVIGraph,
    ctl_key: str,
    *,
    theme: Theme = DEFAULT_THEME,
    theme_mode: ThemeMode = "light",
) -> str | None:
    """Render the standalone ``.ctl`` typedef at graph key ``ctl_key``'s OWN
    front panel to a self-contained SVG string. None when the typedef carries no
    front panel; ValueError when ``ctl_key`` isn't a loaded typedef."""
    front_panel = graph.get_typedef_front_panel(ctl_key)
    if front_panel is None:
        return None
    return render_front_panel_svg(
        front_panel,
        title=graph.get_typedef(ctl_key).name,
        theme=theme,
        theme_mode=theme_mode,
    )

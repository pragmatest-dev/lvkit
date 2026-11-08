"""A front-panel cluster in the Modern look: its frame (``partID`` 9) is a bevel
around the viewport (``partID`` 28), filled with the frame's own color
(``modern.draw_inset_bevel``); the fields are drawn by the cluster glyph on top
of it, without a frame of their own."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyph import ClusterConstantGlyph
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from . import modern
from .base import FRAME_PART_ID, find_part, local_part_rect, value_extent
from .style import ControlStyle, numeric_style

VIEWPORT_PART_ID = 28


@dataclass(frozen=True)
class ModernClusterGlyph:
    """``fields`` drawn by ``inner`` (a cluster glyph with no frame of its own)
    over the bevel from ``frame_local`` to ``viewport_local`` (value-box-local,
    value box of size ``native_size``) filled ``fill``."""

    inner: ClusterConstantGlyph
    frame_local: Rect
    viewport_local: Rect
    native_size: tuple[float, float]
    fill: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        frame = scale_local(self.frame_local, self.native_size, bounds)
        viewport = scale_local(self.viewport_local, self.native_size, bounds)
        if frame is not None and viewport is not None:
            modern.draw_inset_bevel(backend, frame, viewport, self.fill)
        self.inner.draw(backend, bounds, theme)


def modern_cluster(
    ctrl: ParsedFPControl, inner: ClusterConstantGlyph, theme_fill: str
) -> ModernClusterGlyph | None:
    """The Modern drawing of ``ctrl``'s cluster, laid out from its frame and
    viewport parts; None when its frame is not a Modern one or the heap has no
    viewport. ``inner`` draws the fields; ``theme_fill`` fills the viewport when
    the frame stores no color."""
    if numeric_style(ctrl) != ControlStyle.MODERN:
        return None
    frame_part = find_part(ctrl, FRAME_PART_ID)
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    frame = local_part_rect(ctrl, FRAME_PART_ID, origin)
    viewport = local_part_rect(ctrl, VIEWPORT_PART_ID, origin)
    if frame_part is None or frame is None or viewport is None:
        return None
    return ModernClusterGlyph(
        inner=inner,
        frame_local=frame,
        viewport_local=viewport,
        native_size=(x2 - x1, y2 - y1),
        fill=frame_part.fg_color or theme_fill,
    )

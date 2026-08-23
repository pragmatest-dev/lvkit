"""``DecorationGlyph`` — the abstract base for a block-diagram decoration.

A decoration is one of LabVIEW's Decorations-palette shapes (Flat Frame, Thin/
Thick Line, Thin/Thick Line with Arrow) — PURE VISUAL, no data or dataflow. Each
glyph paints itself into a rect (the decoration's ``Layout.node_bounds``). The
ORIENTATION of a line/arrow is taken from that rect (wide -> horizontal, tall ->
vertical, square-ish -> diagonal), so no ``ImageInternalsResID`` decode is needed.

All glyphs are clean-room geometry (a stroked rect, a line, a filled triangle) —
NO NI artwork.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...backend import Backend
from ...style import Theme

Rect = tuple[float, float, float, float]

# Stroke widths for a thin vs thick line/arrow (px). LabVIEW's thick decoration
# is a few px; thin is a hairline.
THIN_W = 1.0
THICK_W = 3.0


Point = tuple[float, float]


def line_endpoints(bounds: Rect) -> tuple[Point, Point]:
    """The (tail, head) of a line/arrow decoration, oriented FROM ITS BOUNDS: a
    wide box is a horizontal line, a tall box vertical, otherwise the box's
    diagonal. ``head`` is the arrow-tip end (arrowheads point up/left/up-left —
    the palette's default; the exact per-instance direction lives in the
    un-decoded ``ImageInternalsResID`` and is refined separately)."""
    x1, y1, x2, y2 = bounds
    w, h = x2 - x1, y2 - y1
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    if w >= h * 3:  # horizontal — head at the left
        return (x2, cy), (x1, cy)
    if h >= w * 3:  # vertical — head at the top
        return (cx, y2), (cx, y1)
    return (x2, y2), (x1, y1)  # diagonal — head at the top-left


class DecorationGlyph(ABC):
    """Base: paint one decoration shape into ``bounds``."""

    @abstractmethod
    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None: ...

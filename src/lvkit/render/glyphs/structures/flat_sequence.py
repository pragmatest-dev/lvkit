"""``FlatSequenceGlyph`` — a flat sequence: opaque body + film-strip rails and
per-frame divider lines."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import RAIL_INSET, THICK_FRAME_BORDER_W, Rect, StructureBodyGlyph

_RAIL_HOLE_SIZE = 4.0
_RAIL_HOLE_STEP = 7.0


class FlatSequenceGlyph(StructureBodyGlyph):
    """Outer box + top/bottom rails + a vertical divider at each inter-frame
    boundary (the film-strip look). ``dividers`` are absolute x-positions,
    injected from the layout; ``border_color`` colours an error-cluster boundary
    (same treatment as case/stacked). ``frame_colors``, when given, is one
    saved background fill per frame (left to right, so ``len(frame_colors)
    == len(dividers) + 1``) -- a flat sequence shows every frame SIDE BY
    SIDE at once (unlike a case/stacked-sequence's one-at-a-time frames), so
    each compartment needs its OWN fill, not one shared color for the whole
    structure (verified against a real corpus VI: its first frame carries a
    real, different bgColor than the rest, and they render as visibly
    distinct cream/white compartments in LabVIEW's own screenshot)."""

    def __init__(
        self,
        *,
        dividers: list[float] | None = None,
        border_color: str | None = None,
        frame_colors: list[str | None] | None = None,
    ) -> None:
        self.dividers = dividers or []
        self.frame_colors = frame_colors
        self._apply_error_border(border_color)

    def draw_body(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        if not self.frame_colors:
            super().draw_body(backend, bounds, theme)
            return
        x1, y1, x2, y2 = bounds
        edges = [x1, *self.dividers, x2]
        for i, color in enumerate(self.frame_colors):
            if i + 1 >= len(edges):
                break
            self.fill_rect(backend, (edges[i], y1, edges[i + 1], y2), color, theme)

    def interior(self, bounds: Rect) -> Rect:
        x1, y1, x2, y2 = bounds
        w = THICK_FRAME_BORDER_W
        top = RAIL_INSET + w / 2
        return (x1 + w, y1 + top, x2 - w, y2 - top)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        s = self.border_color or theme.struct_border
        # The OUTER left/right edges are the SAME real frame BORDER band
        # (THICK_FRAME_BORDER_W wide, light-grey fill, thin black outline) as
        # an inter-frame divider -- per the real reference image, never the
        # base class's generic thin rect. Drawn BEFORE the rails, so the
        # rails paint on top where they cross these bands at the corners.
        # Vertically, the bands run only between the two rails' OWN outer
        # edges -- not the full node bounds -- so a divider ends flush at the
        # rail's stroke instead of poking a bare nub above/below it.
        w = THICK_FRAME_BORDER_W
        half = THICK_FRAME_BORDER_W / 2
        by1 = y1 + RAIL_INSET - half
        by2 = y2 - RAIL_INSET + half
        self._draw_divider_band(backend, theme, x1, x1 + w, by1, by2, s)
        self._draw_divider_band(backend, theme, x2 - w, x2, by1, by2, s)
        # A divider's recorded x is frame i's own LEFT edge (heap-verified: the
        # frame BEFORE it overlaps this exact x by one THICK_FRAME_BORDER_W --
        # the two frames' own <bounds> share a THICK_FRAME_BORDER_W-wide zone
        # starting there), so the band is the LEFT bound of that zone, not its
        # center.
        for dx in self.dividers:
            self._draw_divider_band(backend, theme, dx, dx + w, by1, by2, s)
        self._draw_rails(backend, bounds, theme, s)

    @staticmethod
    def _draw_divider_band(
        backend: Backend,
        theme: Theme,
        bx1: float,
        bx2: float,
        y1: float,
        y2: float,
        stroke: str,
    ) -> None:
        backend.rect(bx1, y1, bx2, y2, fill=theme.film_rail_fill, stroke=stroke)

    def _draw_rails(
        self, backend: Backend, bounds: Rect, theme: Theme, stroke: str
    ) -> None:
        """The film-strip top/bottom rails: a THICK_FRAME_BORDER_W-wide
        light-grey band (not the base class's thin line) with a row of
        punched holes -- real sprocket-hole perforations, per the real
        reference image."""
        x1, y1, x2, y2 = bounds
        half = THICK_FRAME_BORDER_W / 2
        hole_half = _RAIL_HOLE_SIZE / 2
        n = max(1, round((x2 - x1) / _RAIL_HOLE_STEP))
        for ry in (y1 + RAIL_INSET, y2 - RAIL_INSET):
            backend.rect(
                x1, ry - half, x2, ry + half, fill=theme.film_rail_fill, stroke=stroke
            )
            for i in range(n):
                hx = x1 + (i + 0.5) * (x2 - x1) / n
                backend.rect(
                    hx - hole_half,
                    ry - hole_half,
                    hx + hole_half,
                    ry + hole_half,
                    fill=theme.canvas,
                    stroke=stroke,
                    stroke_width=0.5,
                )

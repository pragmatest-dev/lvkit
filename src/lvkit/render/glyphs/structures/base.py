"""``StructureBodyGlyph`` — the abstract base every structure glyph inherits.

A structure glyph paints, for ONE kind of structure:

* an OPAQUE body (the #35/#39 occlusion fix — a later sibling's body covers an
  earlier sibling's whole subtree), and
* the structure's static outline/border decoration (loop cards, frame box,
  sequence rails, event/IPES band).

The glyph is PURE: it imports neither ``scene`` nor ``draw`` and takes only a
backend, a bounds rect, and a theme. Everything scene-derived (an error-cluster
border colour, the disable dotted style, sequence divider positions) is injected
as plain constructor fields by the tree builder, which is the layer allowed to
know the scene. Interactive chrome (the selector widget, value labels, dropdown
menus, border terminals) is NOT a glyph concern — the composite tree draws it.
"""

from __future__ import annotations

from abc import ABC

from ...backend import Backend
from ...style import Theme

Rect = tuple[float, float, float, float]


class StructureBodyGlyph(ABC):
    """Base = the generic structure: an opaque rectangular body + a plain
    border. Subclasses override :meth:`draw_body` (body shape) and/or
    :meth:`draw_outline` (border decoration)."""

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        self.draw_body(backend, bounds, theme)
        self.draw_outline(backend, bounds, theme)

    def draw_body(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Paint the OPAQUE body so the structure occludes what's behind it.

        The default footprint is the whole bounds rect; a kind whose real
        occluding silhouette is smaller than its bounds (a For-loop's stepped
        cards leave transparent notches) overrides this to fill that silhouette
        instead, so a sibling behind the notch shows through."""
        x1, y1, x2, y2 = bounds
        backend.rect(x1, y1, x2, y2, fill=theme.canvas, stroke=None)

    def interior(self, bounds: Rect) -> Rect:
        """The rect the structure clips its CONTENTS to. Default: the whole
        bounds. A kind whose inner diagram is bounded by an inset front card
        (the For-loop) overrides this so contents clip to that card, not to the
        outer stacked-card bounds."""
        return bounds

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Stroke the structure's static border."""
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill="none", stroke=theme.struct_border, stroke_width=1.2
        )

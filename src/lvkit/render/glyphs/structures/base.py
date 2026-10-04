"""``StructureBodyGlyph`` — the abstract base every structure glyph inherits.

A structure glyph paints, for ONE kind of structure:

* an OPAQUE body (the #35/#39 occlusion fix — a later sibling's body covers an
  earlier sibling's whole subtree), and
* the structure's static outline/border decoration (loop cards, frame box,
  sequence rails, event/IPES band).

The glyph is PURE: it imports neither ``scene`` nor ``graph`` nor ``layout`` and
takes only a backend, a bounds rect, and a theme. Everything scene-derived (an
error-cluster border colour, sequence divider positions, the frame values a
selector pages through) is injected as plain values by the composite, which is
the layer allowed to know the scene.

The frame SELECTOR chrome (the ``◄ value ▼ ►`` box, dropdown menu, value labels,
and the type-spec type icon) is owned by :class:`~.selectable.SelectableStructureGlyph`
— the parent of the interactive kinds (case, stacked sequence, event, disable
family) — over an injected ``SelectorState``. Border terminals stay a composite
concern (drawn by ``_draw_border_terminal`` per frame).
"""

from __future__ import annotations

from abc import ABC

from ...backend import Backend
from ...style import Theme

Rect = tuple[float, float, float, float]

# Default structure border stroke width.
DEFAULT_BORDER_W = 1.2
# An error-cluster case/sequence colours its box (green/red by the default
# frame) and draws it slightly bolder than a normal border.
ERROR_BORDER_W = 1.6
# Film-strip rail inset from the top/bottom edges, and rail/divider stroke width
# (flat + stacked sequence).
RAIL_INSET = 4.0
RAIL_W = 1.0
# LabVIEW's bold structure-chrome WIDTH -- shared by every kind of "important
# border" (a While Loop's own border, a flat sequence's real inter-frame
# divider band, its own rail band thickness, a case structure's hashed
# border band), all verified the same width against the real reference
# image. The WIDTH is the one shared thing; each kind still draws its OWN
# fill/pattern style at that width (solid grey, light-grey + thin outline,
# dithered checker, perforated holes) -- never a shared drawing routine.
THICK_FRAME_BORDER_W = 5.2


class StructureBodyGlyph(ABC):
    """Base = the generic structure: an opaque rectangular body + a plain
    border. Subclasses override :meth:`draw_body` (body shape) and/or
    :meth:`draw_outline` (border decoration).

    ``border_width`` is the outline stroke width, and each kind may set its own
    — a While loop draws a thick grey border, a For loop a thin one. The layout
    bounding box is the OUTER edge of the border. ``draw_outline`` is handed the
    OUTER bounds and strokes there: the backend insets every stroked bounded
    shape by half its width (``backend._stroke_inset``), so a plain bordered box
    lands its outer edge on the bounds with no per-kind offset. :meth:`interior`
    clips content a FULL ``border_width`` inside — the border's inner edge — so
    content meets the border flush (no overpaint, no gap). The opaque body still
    fills the whole box. (A kind whose border is a freeform *path*/line rather
    than a bounded shape — the For-loop cards, the sequence rails — isn't
    auto-inset by the backend and pulls its own working rect in by
    ``_stroke_inset``.)"""

    border_width: float = DEFAULT_BORDER_W
    # A scene-injected border colour (error-cluster case/sequence); None uses the
    # theme's default structure border. Set by subclasses that accept it.
    border_color: str | None = None
    # The structure's own saved background fill (LabVIEW's "Background Color"
    # on a loop/frame -- parser.models.ParsedLoopStructure.bg_color / the
    # shared models.Frame.bg_color), scene-injected by the composite. None
    # uses the theme's default canvas fill.
    bg_color: str | None = None

    def _apply_error_border(self, border_color: str | None) -> None:
        """Store a scene-injected border colour and bold the border to match —
        the shared error-cluster boundary treatment (case + sequence)."""
        self.border_color = border_color
        self.border_width = (
            ERROR_BORDER_W if border_color is not None else DEFAULT_BORDER_W
        )

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        self.draw_body(backend, bounds, theme)
        # Pass the OUTER bounds straight through: the backend insets every
        # stroked bounded outline by half its width, so the border's outer edge
        # lands on the box with no pre-inset here (that would double up).
        self.draw_outline(backend, bounds, theme)

    def draw_body(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Paint the OPAQUE body so the structure occludes what's behind it.

        The default footprint is the whole bounds rect; a kind whose real
        occluding silhouette is smaller than its bounds (a For-loop's stepped
        cards leave transparent notches) overrides this to fill that silhouette
        instead, so a sibling behind the notch shows through."""
        self.fill_rect(backend, bounds, self.bg_color, theme)

    @staticmethod
    def fill_rect(
        backend: Backend, rect: Rect, color: str | None, theme: Theme
    ) -> None:
        """The one shared "paint a background rect" op -- ``color`` when the
        structure/frame has its own saved one, else ``theme.canvas``. Used by
        every structure body fill: this class's own default ``draw_body``,
        ``FlatSequenceGlyph``'s per-compartment fill (every frame visible at
        once), and composite.py's per-frame interactive redraw (one frame
        visible at a time, toggled) -- never a one-off inline ``backend.rect``
        at each of those call sites."""
        x1, y1, x2, y2 = rect
        backend.rect(x1, y1, x2, y2, fill=color or theme.canvas, stroke=None)

    def interior(self, bounds: Rect) -> Rect:
        """The rect the structure clips its CONTENTS to. Default: the whole
        bounds, pulled inside the border by half its stroke width so content
        meets the border's inner edge flush. A kind whose inner diagram is
        bounded by an inset front card (the For-loop) overrides this to clip to
        that card (also border-inset), not the outer stacked-card bounds."""
        return self._clip_inset(bounds)

    def _clip_inset(self, rect: Rect) -> Rect:
        """Shrink a clip rect by a FULL border width on every side — the border
        sits fully inside the box, so its inner edge is one width in."""
        x1, y1, x2, y2 = rect
        i = self.border_width
        return (x1 + i, y1 + i, x2 - i, y2 - i)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Stroke the structure's static border box (colour + width from the
        kind's own ``border_color``/``border_width``)."""
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill="none",
            stroke=self.border_color or theme.struct_border,
            stroke_width=self.border_width,
        )

    def _draw_rails(self, backend: Backend, bounds: Rect, stroke: str) -> None:
        """Draw the film-strip top/bottom rails shared by flat + stacked
        sequences, ``RAIL_INSET`` in from the box's top and bottom edges."""
        x1, y1, x2, y2 = bounds
        for ry in (y1 + RAIL_INSET, y2 - RAIL_INSET):
            backend.line(x1, ry, x2, ry, stroke=stroke, stroke_width=RAIL_W)


# Dithered-checker tile size -- small enough that the two alternating colours
# read as one averaged grey from a normal viewing distance, per the real
# reference image, without actually being a solid fill.
_DITHER_TILE = 1.0


def draw_dither_band(
    backend: Backend, bounds: Rect, width: float, dark: str, light: str
) -> None:
    """A real frame BORDER band (perimeter strip, ``width`` wide) filled with
    a dense two-colour checkerboard dither -- a case structure's own border,
    per the real reference image. A pure drawing primitive (like
    ``StructureBodyGlyph.fill_rect``): which colours/width to use is each
    glyph's own decision, not shared drawing logic."""
    x1, y1, x2, y2 = bounds
    for strip in (
        (x1, y1, x2, y1 + width),  # top
        (x1, y2 - width, x2, y2),  # bottom
        (x1, y1 + width, x1 + width, y2 - width),  # left (between top/bottom)
        (x2 - width, y1 + width, x2, y2 - width),  # right (between top/bottom)
    ):
        sx1, sy1, sx2, sy2 = strip
        if sx2 <= sx1 or sy2 <= sy1:
            continue
        nx = max(1, round((sx2 - sx1) / _DITHER_TILE))
        ny = max(1, round((sy2 - sy1) / _DITHER_TILE))
        tw, th = (sx2 - sx1) / nx, (sy2 - sy1) / ny
        for j in range(ny):
            for i in range(nx):
                tx1, ty1 = sx1 + i * tw, sy1 + j * th
                fill = dark if (i + j) % 2 == 0 else light
                backend.rect(tx1, ty1, tx1 + tw, ty1 + th, fill=fill, stroke="none")

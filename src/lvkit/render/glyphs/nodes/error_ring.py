"""An Error Ring XNode, drawn as LabVIEW shows a configured one.

Measured from NI's public Error Ring page image (labview-api-ref,
``vi-lib/errorring/loc_ErrorRing.gif``; a 22-pixel-high ring, the height of its
error out terminal): a white body with a 2-pixel border; on its left
the error / warning toggle (a 15-pixel round mark starting 3 pixels inside the
outer edge) and the call-chain toggle (5 pixels wide, starting 21 pixels in);
the ring's text from 28 pixels in, vertically centred; a 7-by-4 dropdown arrow
whose right edge is 4 pixels inside the ring's outer right edge; the error out
terminal right of the body, and a ring's format inputs below it. The marks are
lvkit's own drawings, not NI's artwork.

Each part is read from the node (``_error_ring_glyph``): the text is the label
LabVIEW stores in the ring's StateData (``ErrorRing.label``), the round mark
the error / warning byte, the call-chain mark the call-chain byte, and the
body's right edge and bottom the error out terminal's ``termBounds`` left edge
and bottom.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label

# Pixel offsets measured on NI's image, from the ring's outer left edge.
BORDER = 2.0
TOGGLE_LEFT = 3.0
TOGGLE_SIZE = 15.0
CHAIN_LEFT = 21.0
CHAIN_WIDTH = 5.0
TEXT_LEFT = 28.0
ARROW_WIDTH = 7.0
ARROW_HEIGHT = 4.0
ARROW_RIGHT_INSET = 4.0
TEXT_SIZE = 9.0


@dataclass(frozen=True)
class ErrorRingGlyph:
    """``label`` is the ring's text; ``is_warning`` picks the warning mark;
    ``call_chain`` draws the call-chain mark; ``body_right`` and ``body_bottom``
    are the fractions of the node's width and height where the body ends: the
    error out terminal's left edge and bottom."""

    label: str
    is_warning: bool
    call_chain: bool
    body_right: float = 1.0
    body_bottom: float = 1.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        right = x1 + (x2 - x1) * self.body_right
        y2 = y1 + (y2 - y1) * self.body_bottom
        mid = (y1 + y2) / 2
        backend.rect(
            x1 + BORDER / 2,
            y1 + BORDER / 2,
            right - BORDER / 2,
            y2 - BORDER / 2,
            fill=theme.const_fill,
            stroke=theme.error_ring_stroke,
            stroke_width=BORDER,
        )
        self._draw_toggle(backend, x1 + TOGGLE_LEFT, mid, theme)
        if self.call_chain:
            self._draw_chain(backend, x1 + CHAIN_LEFT, mid, theme)
        arrow_right = right - ARROW_RIGHT_INSET
        text_room = arrow_right - ARROW_WIDTH - (x1 + TEXT_LEFT)
        backend.text(
            x1 + TEXT_LEFT,
            mid + TEXT_SIZE * 0.35,
            fit_label(self.label, text_room, backend, TEXT_SIZE),
            TEXT_SIZE,
            fill=theme.const_text,
            anchor="start",
        )
        top = mid - ARROW_HEIGHT / 2
        backend.polygon(
            [
                (arrow_right - ARROW_WIDTH, top),
                (arrow_right, top),
                (arrow_right - ARROW_WIDTH / 2, top + ARROW_HEIGHT),
            ],
            fill=theme.error_ring_arrow,
        )

    def _draw_toggle(
        self, backend: Backend, left: float, mid: float, theme: Theme
    ) -> None:
        """The error / warning toggle: a round mark with a cross for an error,
        with an exclamation bar for a warning."""
        r = TOGGLE_SIZE / 2
        cx = left + r
        backend.circle(
            cx,
            mid,
            r,
            fill=theme.error_ring_warning
            if self.is_warning
            else theme.error_ring_error,
        )
        d = r * 0.45
        mark = theme.error_ring_mark
        if self.is_warning:
            backend.line(cx, mid - d, cx, mid + d * 0.4, stroke=mark, stroke_width=2.0)
            backend.circle(cx, mid + d, 1.0, fill=mark)
        else:
            backend.line(
                cx - d, mid - d, cx + d, mid + d, stroke=mark, stroke_width=2.0
            )
            backend.line(
                cx - d, mid + d, cx + d, mid - d, stroke=mark, stroke_width=2.0
            )

    def _draw_chain(
        self, backend: Backend, left: float, mid: float, theme: Theme
    ) -> None:
        """The call-chain toggle: two linked rings, one above the other."""
        r = CHAIN_WIDTH / 2
        cx = left + r
        for cy in (mid - r * 1.1, mid + r * 1.1):
            backend.circle(
                cx,
                cy,
                r * 0.8,
                fill=None,
                stroke=theme.error_ring_chain,
                stroke_width=1.0,
            )

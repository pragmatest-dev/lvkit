"""The value glyph for a ``stdSlide`` control."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme

_TRACK_THICKNESS = 3.0
_THUMB_RADIUS = 4.0
_TRACK_PADDING_FRACTION = 0.3

# A recorded 0..UINT32_MAX (or similar full-width-integer) range is LabVIEW's
# OWN "no bound was set" default for that numeric subtype, the same as an
# explicit -inf/inf -- not a real, deliberately narrow range. This module
# does not try to detect/exclude it (that needs guessing each numeric
# subtype's own default range), so a slide left at its type's full default
# range draws a thumb at value/range like any other bounded slide would; no
# real corpus slide with such a range also carries a saved value today.


def _fraction(value: float | None, lo: float | None, hi: float | None) -> float | None:
    """``value``'s position between ``lo`` and ``hi`` as 0..1, or ``None``
    when any of the three isn't a usable, finite number, or the range is
    empty/inverted -- never a guessed midpoint."""
    if value is None or lo is None or hi is None:
        return None
    if not (math.isfinite(value) and math.isfinite(lo) and math.isfinite(hi)):
        return None
    if hi <= lo:
        return None
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


@dataclass(frozen=True)
class SlideControlGlyph:
    """A frame + a thin recessed track along the control's long axis
    (orientation read from its own box, never assumed), with a thumb at the
    saved value's real position between its own recorded ``StdNumMin``/
    ``StdNumMax`` -- only when that position is actually computable (a finite
    range and a plain decoded number); otherwise just the frame and track,
    never a guessed position. Scale tick marks and an optional digital
    display are separate heap parts this glyph does not draw."""

    border_color: str
    fraction: float | None  # the thumb's 0..1 position, or None

    @classmethod
    def from_value(
        cls,
        border_color: str,
        value: float | None,
        lo: float | None,
        hi: float | None,
    ) -> SlideControlGlyph:
        """Built from the control's own raw saved value and recorded range --
        see ``_fraction`` for when a thumb position is actually computable."""
        return cls(border_color, _fraction(value, lo, hi))

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.fp_value_fill, stroke=self.border_color, stroke_width=1.0,
        )
        horizontal = (x2 - x1) >= (y2 - y1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        pad = min(x2 - x1, y2 - y1) * _TRACK_PADDING_FRACTION
        if horizontal:
            tx1, tx2 = x1 + pad, x2 - pad
            backend.rect(
                tx1, cy - _TRACK_THICKNESS / 2, tx2, cy + _TRACK_THICKNESS / 2,
                fill=theme.pane_type_text, stroke="none",
            )
            if self.fraction is not None:
                backend.circle(
                    tx1 + (tx2 - tx1) * self.fraction, cy, _THUMB_RADIUS,
                    fill=theme.fp_value_fill, stroke=self.border_color,
                    stroke_width=1.2,
                )
        else:
            ty1, ty2 = y1 + pad, y2 - pad
            backend.rect(
                cx - _TRACK_THICKNESS / 2, ty1, cx + _TRACK_THICKNESS / 2, ty2,
                fill=theme.pane_type_text, stroke="none",
            )
            if self.fraction is not None:
                # A slide's origin is its BOTTOM (0 at the bottom, max at the
                # top) -- the same convention a real vertical slider/progress
                # control uses; 1 - fraction converts "distance from lo" to
                # "distance from the top edge" this glyph draws downward from.
                backend.circle(
                    cx, ty2 - (ty2 - ty1) * self.fraction, _THUMB_RADIUS,
                    fill=theme.fp_value_fill, stroke=self.border_color,
                    stroke_width=1.2,
                )

"""``InPlaceGlyph`` — an In Place Element Structure: the same filled band as an
Event Structure, narrower (its decompose/recompose border nodes seat in the
band)."""

from __future__ import annotations

from .event import EventGlyph

# Band width MEASURED from the reference IPES screenshots — 3px (vs the Event
# Structure's 7px).
IPES_BAND_W = 3.0


class InPlaceGlyph(EventGlyph):
    """An IPES draws the identical band border as an Event Structure, only
    narrower — so it reuses ``EventGlyph`` with a fixed 3px band."""

    def __init__(self, *, band_width: float = IPES_BAND_W) -> None:
        super().__init__(band_width=band_width)

"""``EventGlyph`` — an Event Structure: opaque body + a filled band border."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph

# Band width MEASURED from authoritative 1:1 LabVIEW reference screenshots (the
# "[0] Timeout" event structure): a 7px pale band between two 1px edge rules.
EVENT_BAND_W = 7.0


class EventGlyph(StructureBodyGlyph):
    """A filled BAND margin (``theme.event_band``) of ``band_width`` LV units
    between the outer bounds and an inner rule (both in ``theme.event_border``),
    mirroring LabVIEW's wide border. The band is confined to the edge margin, so
    it never covers an interior wire; the opaque body behind it does the
    occluding. Registered event-source glyphs and the frame selector are tree
    chrome, drawn on top."""

    def __init__(self, *, band_width: float = EVENT_BAND_W) -> None:
        self.band_width = band_width

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w = max(2.0, min(self.band_width, (x2 - x1) / 2 - 1.0, (y2 - y1) / 2 - 1.0))
        fill = theme.event_band
        backend.rect(x1, y1, x2, y1 + w, fill=fill, stroke="none")  # top
        backend.rect(x1, y2 - w, x2, y2, fill=fill, stroke="none")  # bottom
        backend.rect(x1, y1 + w, x1 + w, y2 - w, fill=fill, stroke="none")  # left
        backend.rect(x2 - w, y1 + w, x2, y2 - w, fill=fill, stroke="none")  # right
        backend.rect(
            x1, y1, x2, y2, fill="none", stroke=theme.event_border, stroke_width=1.2
        )
        backend.rect(
            x1 + w, y1 + w, x2 - w, y2 - w, fill="none", stroke=theme.event_border,
            stroke_width=1.0,
        )

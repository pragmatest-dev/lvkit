"""``EventGlyph`` — an Event Structure: opaque body + a filled band border."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph

# Band width MEASURED from authoritative 1:1 LabVIEW reference screenshots (the
# "[0] Timeout" event structure): a 7px pale band between two 1px edge rules.
EVENT_BAND_W = 7.0
_RULE_W = 1.0  # both band edge rules are 1px (per the reference)


class EventGlyph(StructureBodyGlyph):
    """A filled BAND margin (``theme.event_band``) of ``band_width`` LV units
    between the outer bounds and an inner rule (both in ``theme.event_border``),
    mirroring LabVIEW's wide border. The band is confined to the edge margin, so
    it never covers an interior wire; the opaque body behind it does the
    occluding. Registered event-source glyphs and the frame selector are tree
    chrome, drawn on top."""

    band_width: float = EVENT_BAND_W

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        # The band is a FILLED region reaching the true bounds, NOT a centered
        # stroke, so it does not use the base's border_width/2 stroke-inset:
        # draw_outline gets the OUTER bounds, matching interior().
        self.draw_body(backend, bounds, theme)
        self.draw_outline(backend, bounds, theme)

    def _band(self, bounds: Rect) -> float:
        """The band's actual width, clamped so it never exceeds half the box."""
        x1, y1, x2, y2 = bounds
        return max(2.0, min(self.band_width, (x2 - x1) / 2 - 1.0, (y2 - y1) / 2 - 1.0))

    def interior(self, bounds: Rect) -> Rect:
        # The BAND is the effective border, so contents clip inside it (not just
        # the 1.2px default) — otherwise an interior wire paints over the band.
        x1, y1, x2, y2 = bounds
        w = self._band(bounds)
        return (x1 + w, y1 + w, x2 - w, y2 - w)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w = self._band(bounds)
        fill = theme.event_band
        backend.rect(x1, y1, x2, y1 + w, fill=fill, stroke="none")  # top
        backend.rect(x1, y2 - w, x2, y2, fill=fill, stroke="none")  # bottom
        backend.rect(x1, y1 + w, x1 + w, y2 - w, fill=fill, stroke="none")  # left
        backend.rect(x2 - w, y1 + w, x2, y2 - w, fill=fill, stroke="none")  # right
        backend.rect(
            x1, y1, x2, y2, fill="none", stroke=theme.event_border,
            stroke_width=_RULE_W,
        )
        backend.rect(
            x1 + w, y1 + w, x2 - w, y2 - w, fill="none", stroke=theme.event_border,
            stroke_width=_RULE_W,
        )

"""``TunnelTerminalGlyph`` — a plain data tunnel: a solid block in the wire-type
color, with a per-frame "use default if unwired" hole."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class TunnelTerminalGlyph(BorderTerminalGlyph):
    """A solid type-colored block. In a frame that leaves this OUTPUT tunnel
    unwired (``frame_value`` in ``unwired_frames``), LabVIEW punches a small
    canvas hole (the frame emits the type default); it stays solid in the frames
    that wire it, so this glyph is redrawn per frame."""

    def __init__(self, color: str | None, unwired_frames: frozenset[str]) -> None:
        self.color = color
        self.unwired_frames = unwired_frames

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        col = self.color or theme.wire_default
        backend.rect(x1, y1, x2, y2, fill=col, stroke="#333333", stroke_width=0.75)
        if frame_value is not None and frame_value in self.unwired_frames:
            hw = (x2 - x1) * 0.17
            hh = (y2 - y1) * 0.17
            backend.rect(
                cx - hw, cy - hh, cx + hw, cy + hh, fill=theme.canvas, stroke="none"
            )

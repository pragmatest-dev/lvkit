from __future__ import annotations

import math
from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass
class ControlRefConstGlyph:
    """A Control Reference Constant (class="ctlRefConst"): a box bordered in the
    **referenced control's DATA-TYPE color** (boolean green, numeric orange, …),
    holding a small **reference arrow** and the **type text** (``TF``/``DBL``/
    ``abc``/…, the same ``type_repr`` we stamp on FP terminals) in that type
    color — with the control **name as a label ABOVE** the box. Its OUTPUT wire
    is a refnum (the reference wire color); the border is NOT the refnum color:
    LabVIEW type-colors the constant by the CONTROL it references, and only the
    wire it feeds is a reference wire.

    Distinct from a Local Variable (which carries a ▶ read/write badge this does
    NOT). Clean-room: the arrow and box are our own shapes (LabVIEW draws the
    control's own icon in the box); the type text and name are real data.
    ``type_color`` is the referenced control's type wire color; None falls back
    to the neutral stroke.
    """

    name: str
    type_text: str = ""
    type_color: str | None = None
    fill_attr: str = "localvar_fill"
    stroke_attr: str = "localvar_stroke"
    text_attr: str = "localvar_text"
    text_size: float = 7.0

    _STROKE_W = 1.6
    _ARROW_COLOR = "#111111"  # black shortcut/link-overlay arrow

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        color = self.type_color or getattr(theme, self.stroke_attr)
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=color,
            stroke_width=self._STROKE_W,
        )
        h = y2 - y1
        cy = (y1 + y2) / 2
        pad = 2.5
        text_left = x1 + pad
        s = min(h - 3.0, 12.0)
        if s >= 6.0:
            self._shortcut_arrow(backend, x1 + 2.0, cy - s / 2, s)
            text_left = x1 + 2.0 + s + 2.0
        # The TYPE (TF / DBL / abc / …) fills the box, in the type color — this
        # IS what LabVIEW draws inside a control-reference constant.
        if self.type_text:
            backend.text(
                (text_left + x2 - pad) / 2,
                cy + self.text_size * 0.34,
                self.type_text,
                self.text_size,
                fill=color,
                anchor="middle",
            )
        # The control NAME is a label ABOVE the box (LabVIEW's own placement),
        # in the neutral label color.
        if self.name:
            backend.text(
                (x1 + x2) / 2,
                y1 - 2.0,
                self.name,
                self.text_size,
                fill=getattr(theme, self.text_attr),
                anchor="middle",
            )

    def _shortcut_arrow(
        self,
        backend: Backend,
        ix: float,
        iy: float,
        s: float,
    ) -> None:
        """A black Windows-style shortcut/link overlay: a curved arrow rising
        from the lower-left and hooking to point up-right (the reference mark)."""
        c = self._ARROW_COLOR
        p0 = (ix + 0.18 * s, iy + 0.86 * s)  # tail, lower-left
        cp = (ix + 0.18 * s, iy + 0.30 * s)  # control -> hook up the left side
        p1 = (ix + 0.86 * s, iy + 0.20 * s)  # tip, upper-right
        n = 8
        pts = [
            (
                (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * cp[0] + t * t * p1[0],
                (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * cp[1] + t * t * p1[1],
            )
            for t in (i / n for i in range(n + 1))
        ]
        backend.path(pts, stroke=c, stroke_width=1.2, fill="none")
        # Arrowhead at the tip, aligned to the final tangent (cp -> p1).
        ang = math.atan2(p1[1] - cp[1], p1[0] - cp[0])
        ah = 0.34 * s
        backend.polygon(
            [
                p1,
                (p1[0] - ah * math.cos(ang - 0.5), p1[1] - ah * math.sin(ang - 0.5)),
                (p1[0] - ah * math.cos(ang + 0.5), p1[1] - ah * math.sin(ang + 0.5)),
            ],
            fill=c,
            stroke=None,
        )

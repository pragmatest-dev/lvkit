from __future__ import annotations

from dataclasses import dataclass

from ....models import LVType
from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme, wire_style
from .base import fit_label


@dataclass(frozen=True)
class EventDataGlyph:
    """An Event Structure's Event Data Node / Event Filter Node (heap class
    ``eventDataNode`` for BOTH — see parser/nodes/event.py). Faithful to the
    reference LabVIEW screenshot: a WHITE box (never the tan Bundle/Unbundle-
    By-Name look — this isn't a real cluster assemble/disassemble), one row
    per accessed field, each row's NAME colored by that field's OWN LVType via
    ``wire_style()`` — the same data-driven table that colors wires (int blue,
    string pink, refnum green, ...) — never a fixed palette. A single thin
    vertical accent band marks the node's OUTER edge: the Data Node sits on
    the frame's data-in side (fields flow OUT of it into the diagram), so its
    band is on the LEFT; the Filter Node sits on the opposite side (the
    diagram writes INTO its filterable fields), so its band is on the RIGHT.
    ``is_filter`` (resolved by the caller from ``EventStructureNode.
    filter_node_uids`` — same heap class either way, see render/nodes.py)
    picks which side.
    """

    rows: tuple[tuple[str, LVType | None], ...]
    is_filter: bool = False
    fill_attr: str = "const_fill"  # white
    stroke_attr: str = "tunnel_border"
    band_attr: str = "tunnel_border"
    text_size: float = 7.5

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=stroke,
            stroke_width=1.2,
        )
        rows = self.rows or (("", None),)
        n = len(rows)
        row_h = (y2 - y1) / n
        lsize = max(5.0, min(self.text_size, row_h * 0.62))

        band_w = max(2.0, min(5.0, (x2 - x1) * 0.10))
        band = getattr(theme, self.band_attr)
        if self.is_filter:
            backend.rect(x2 - band_w, y1, x2, y2, fill=band, stroke="none")
            text_x1, text_x2 = x1 + 3.0, x2 - band_w - 3.0
        else:
            backend.rect(x1, y1, x1 + band_w, y2, fill=band, stroke="none")
            text_x1, text_x2 = x1 + band_w + 3.0, x2 - 3.0
        avail = max(2.0, text_x2 - text_x1)

        for i, (name, lv_type) in enumerate(rows):
            ry1 = y1 + i * row_h
            ry2 = ry1 + row_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=0.75)
            color = wire_style(lv_type, theme).color
            label = fit_label(name, avail, backend, lsize)
            backend.text(
                text_x1,
                (ry1 + ry2) / 2 + lsize * 0.34,
                label,
                lsize,
                anchor="start",
                fill=color,
            )

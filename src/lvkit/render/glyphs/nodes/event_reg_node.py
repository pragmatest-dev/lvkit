"""``EventRegNodeGlyph`` -- a Register-For-Events node (heap class
``eventRegNode``, task #56, reference image #68).

A property-node-style box: a HEADER band (gear + this node's own heap-
recorded name, e.g. "Reg Events" -- see ``parser.node_types.EventRegNode``'s
class docstring; NEVER a hard-coded "Register For Events"/"Unregister For
Events" guess) above a GROWABLE drawer of one row per registered event
source ("event 1", "event 2", ... -- always an INPUT, so always a LEFT
arrow, unlike a property row which can be read or write). The LAST row
carries a small "▼" grow-handle mark (reference #68), LabVIEW's own cue that
this drawer can be resized to register more sources.

The event-registration-refnum (in/out) and error (in/out) terminals thread
the box edges at the header level, placed by the scene from the node's real
heap terminal geometry -- not drawn here, same as ``PropertyNodeGlyph``'s
reference/error terminals."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _ROW_ARROW_W, _draw_drawer_row, fit_label


@dataclass(frozen=True)
class EventRegNodeGlyph:
    """``row_count`` is the number of growable "event N" rows (from
    ``PrimitiveNode.event_row_terminal_ids`` -- fully data-driven, never a
    hard-coded count). ``class_name`` is this node's own heap-recorded name
    (``object_name``, from ``<nodeName>``)."""

    row_count: int
    class_name: str = ""
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        backend.rect(
            x1, y1, x2, y2,
            fill=getattr(theme, self.fill_attr),
            stroke=stroke,
            stroke_width=1.2,
        )
        rows = max(1, self.row_count)
        # One header cell (the class/reference row) + one cell per event row
        # -- same uniform-division convention as PropertyNodeGlyph, so a box
        # taller than its minimal content stretches evenly rather than
        # leaving blank space.
        cell_h = (y2 - y1) / (rows + 1)
        lpad = 3.0
        lsize = max(5.0, min(9.0, cell_h * 0.62) - 1.0)

        # Header band: this node's own heap-recorded name, centered, with a
        # divider beneath -- the row the event-registration-refnum + error
        # terminals thread through (placed by the scene at the box edges).
        hy2 = y1 + cell_h
        header = f"⚙ {self.class_name}".strip() if self.class_name else "⚙ Reg Events"
        backend.text(
            (x1 + x2) / 2,
            y1 + cell_h / 2 + lsize * 0.34,
            fit_label(header, (x2 - x1) - 2 * lpad, backend, lsize),
            lsize,
            fill=text_fill,
        )
        backend.line(x1, hy2, x2, hy2, stroke=stroke, stroke_width=1.0)

        # Growable drawer: one "event N" row per registered source, always
        # an INPUT (left arrow only -- an event source is registered ON,
        # never read back through this node). The LAST row carries the
        # grow-handle.
        for i in range(rows):
            ry1 = hy2 + i * cell_h
            ry2 = hy2 + (i + 1) * cell_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=1.0)
            _draw_drawer_row(
                backend,
                x1,
                x2,
                ry1,
                ry2,
                f"event {i + 1}",
                show_left=True,
                show_right=False,
                text_fill=text_fill,
                lsize=lsize,
            )
            if i == rows - 1:
                cy = (ry1 + ry2) / 2
                backend.text(
                    x2 - _ROW_ARROW_W * 0.55,
                    cy + lsize * 0.34,
                    "▼",
                    lsize,
                    fill=text_fill,
                )

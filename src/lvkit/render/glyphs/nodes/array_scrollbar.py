"""The array's vertical scrollbar: track, arrow buttons at each end, and a
thumb sized to the visible/total fraction.

Shared by the block-diagram array constant and the front-panel array control;
the caller supplies the track rect (the heap's own scrollbar part).
"""

from __future__ import annotations

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


def draw_array_scrollbar(
    backend: Backend,
    track: Rect,
    theme: Theme,
    struct_uid: str,
    total: int,
    visible: int,
) -> None:
    tx1, ty1, tx2, ty2 = track
    backend.rect(
        tx1, ty1, tx2, ty2,
        fill=theme.fp_value_fill,
        stroke=theme.struct_border,
        stroke_width=0.75,
    )
    track_h = ty2 - ty1
    track_w = tx2 - tx1
    if track_h <= 0 or track_w <= 0:
        return
    arrow_h = min(track_w, track_h / 3.0)
    cy_top, cy_bot = ty1 + arrow_h / 2, ty2 - arrow_h / 2
    cx = (tx1 + tx2) / 2
    aw = track_w * 0.3
    backend.polygon(
        [(cx - aw, cy_top + aw * 0.6), (cx + aw, cy_top + aw * 0.6),
         (cx, cy_top - aw * 0.6)],
        fill=theme.struct_border,
    )
    backend.polygon(
        [(cx - aw, cy_bot - aw * 0.6), (cx + aw, cy_bot - aw * 0.6),
         (cx, cy_bot + aw * 0.6)],
        fill=theme.struct_border,
    )
    scroll_y1, scroll_y2 = ty1 + arrow_h, ty2 - arrow_h
    scroll_h = scroll_y2 - scroll_y1
    if scroll_h <= 0:
        return
    frac = min(1.0, visible / total) if total > 0 else 1.0
    thumb_h = max(scroll_h * frac, min(scroll_h, 10.0))
    thumb_y2 = min(scroll_y2, scroll_y1 + thumb_h)
    backend.begin_group(cls="lv-array-scrollbar", data={"lv-struct": struct_uid})
    backend.rect(
        tx1 + 1.5, scroll_y1 + 1.0, tx2 - 1.5, thumb_y2 - 1.0,
        fill=theme.fp_index_fill,
        stroke=theme.struct_border,
        stroke_width=0.5,
    )
    backend.end_group()

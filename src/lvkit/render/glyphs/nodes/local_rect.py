"""Map a control's OWN heap-local rect onto wherever the control is drawn.

Heap part rects are relative to the control's own (0, 0) at its NATIVE size --
the same contract ``ClusterGeom`` documents: one uniform scale
``s = min(box_w / native_w, box_h / native_h)``, pure translation when the box
already is the native size.
"""

from __future__ import annotations

from ....parser.layout import Rect


def scale_local(
    local: Rect | None,
    native_size: tuple[float, float] | None,
    bounds: Rect,
) -> Rect | None:
    """``local`` placed inside ``bounds``; ``None`` when ``local`` or
    ``native_size`` is unknown (or degenerate)."""
    if local is None or native_size is None:
        return None
    native_w, native_h = native_size
    if native_w <= 0 or native_h <= 0:
        return None
    x1, y1, x2, y2 = bounds
    scale = min((x2 - x1) / native_w, (y2 - y1) / native_h)
    lx1, ly1, lx2, ly2 = local
    return (
        x1 + scale * lx1,
        y1 + scale * ly1,
        x1 + scale * lx2,
        y1 + scale * ly2,
    )

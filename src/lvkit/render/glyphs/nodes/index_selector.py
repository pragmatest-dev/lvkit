"""The array index selector: ``▲``/``▼`` decrement/increment click targets and
the live index readout, drawn into one box.

Shared by the block-diagram array constant and the front-panel array control;
each decides WHERE (and whether) its selector is drawn, this only draws it.
The readout is updated live by the array controller JS (``0`` with no JS).
"""

from __future__ import annotations

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label


def draw_index_selector(
    backend: Backend, box: Rect, theme: Theme, struct_uid: str
) -> None:
    ix1, iy1, ix2, iy2 = box
    backend.rect(
        ix1, iy1, ix2, iy2,
        fill=theme.case_bar_fill,
        stroke=theme.struct_border,
        stroke_width=0.75,
    )
    arrow_w = 8.0
    mid = (iy1 + iy2) / 2
    # ▲ (top) = index UP / next (index + 1); ▼ (bottom) = index down / prev.
    backend.begin_group(
        cls="lv-selector lv-clickable",
        data={"lv-action": "next", "lv-struct": struct_uid},
    )
    backend.rect(ix1, iy1, ix1 + arrow_w, mid, fill="transparent", stroke="none")
    backend.polygon(
        [
            (ix1 + arrow_w / 2, iy1 + 2.5),
            (ix1 + 1.5, mid - 1.5),
            (ix1 + arrow_w - 1.5, mid - 1.5),
        ],
        fill=theme.case_bar_text,
    )
    backend.end_group()
    backend.begin_group(
        cls="lv-selector lv-clickable",
        data={"lv-action": "prev", "lv-struct": struct_uid},
    )
    backend.rect(ix1, mid, ix1 + arrow_w, iy2, fill="transparent", stroke="none")
    backend.polygon(
        [
            (ix1 + 1.5, mid + 1.5),
            (ix1 + arrow_w - 1.5, mid + 1.5),
            (ix1 + arrow_w / 2, iy2 - 2.5),
        ],
        fill=theme.case_bar_text,
    )
    backend.end_group()
    size = min(9.0, (iy2 - iy1) * 0.7)
    label = fit_label("0", (ix2 - (ix1 + arrow_w)) - 2.0, backend, size)
    backend.begin_group(cls="lv-array-index", data={"lv-struct": struct_uid})
    backend.text(
        (ix1 + arrow_w + ix2) / 2,
        mid + size * 0.34,
        label,
        size,
        fill=theme.case_bar_text,
    )
    backend.end_group()


def draw_index_control(
    backend: Backend,
    spinner: Rect,
    readout: Rect,
    theme: Theme,
    struct_uid: str,
) -> None:
    """The index as LabVIEW draws it: a compact rounded spinner (an up half and
    a down half) beside a SEPARATE readout box -- not one box holding both.
    The caller supplies each piece's rect (the index part's own sub-parts)."""
    draw_spinner(backend, spinner, theme, struct_uid)
    rx1, ry1, rx2, ry2 = readout
    backend.rect(
        rx1, ry1, rx2, ry2,
        fill=theme.fp_value_fill,
        stroke=theme.struct_border,
        stroke_width=0.75,
    )
    size = min(11.0, (ry2 - ry1) * 0.7)
    backend.begin_group(cls="lv-array-index", data={"lv-struct": struct_uid})
    backend.text(
        (rx1 + rx2) / 2,
        (ry1 + ry2) / 2 + size * 0.34,
        "0",
        size,
        fill=theme.fp_value_text,
    )
    backend.end_group()


def draw_spinner(
    backend: Backend, rect: Rect, theme: Theme, struct_uid: str | None = None
) -> None:
    """A compact rounded spinner: an up half and a down half. With a
    ``struct_uid`` each half is an array-controller click target; without one
    it is a static control's spinner."""
    sx1, sy1, sx2, sy2 = rect
    width = sx2 - sx1
    mid = (sy1 + sy2) / 2
    backend.rect(
        sx1, sy1, sx2, sy2,
        fill=theme.fp_index_fill,
        stroke=theme.struct_border,
        stroke_width=0.75,
        rx=width / 2,
    )
    half_w = width * 0.28
    half_h = min(width * 0.3, (mid - sy1) * 0.45)
    cx = (sx1 + sx2) / 2
    for action, lo, hi, up in (("next", sy1, mid, True), ("prev", mid, sy2, False)):
        cy = (lo + hi) / 2
        tip, base = (cy - half_h, cy + half_h) if up else (cy + half_h, cy - half_h)
        if struct_uid is not None:
            backend.begin_group(
                cls="lv-selector lv-clickable",
                data={"lv-action": action, "lv-struct": struct_uid},
            )
            backend.rect(sx1, lo, sx2, hi, fill="transparent", stroke="none")
        backend.polygon(
            [(cx - half_w, base), (cx + half_w, base), (cx, tip)],
            fill=theme.struct_border,
        )
        if struct_uid is not None:
            backend.end_group()

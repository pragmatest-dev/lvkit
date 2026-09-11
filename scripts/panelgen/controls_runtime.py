"""Runtime controls for LabVIEW front-panel types, composed from NATIVE NiceGUI
elements (ui.column/ui.row/ui.input/ui.number/ui.button) — no custom Vue.

Copied verbatim into each generated panel dir as ``controls.py`` so a generated
panel stays self-contained (the gallery loads panels standalone). ``panel.py``
imports ``array_control`` from here for any array-typed control/indicator.

The array control mirrors the way lvkit already draws a LabVIEW array
(``render/glyphs/nodes/array_constant.py``): an INDEX CONTROL on the left (▲/▼
plus a numeric index) and, to its right, a viewport showing a WHOLE NUMBER of
element cells — ``floor(box_height / _CELL_H)`` — starting at the current index.
It is index-driven, NOT a free scrollbar, so you never see half-height rows;
stepping the index pages the window through the array. Typing into the first
past-the-end (greyed) cell appends, the way a LabVIEW array grows.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui

# Element-cell height and index-column width, echoing the array glyph's
# _CELL_H/_INDEX_W so the front panel reads like the block-diagram array.
_CELL_H = 22.0
_INDEX_W = 40.0


def _coerce(text: str) -> Any:
    """Parse a cell back to int, then float, else keep the string — so numeric
    arrays round-trip as numbers and string arrays stay strings."""
    t = text.strip()
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return text


def array_control(
    state: Any,
    field: str,
    *,
    readonly: bool = False,
    label: str = "",
    height: float = 3 * _CELL_H,
) -> Callable[[], None]:
    """1D array bound to ``state.<field>`` (a list), drawn like a LabVIEW array
    control: an INDEX CONTROL (▲/▼ + a numeric index) on the left and a viewport
    of ``visible = floor(height / _CELL_H)`` WHOLE element cells on the right,
    showing ``state.<field>[index : index + visible]``. Stepping the index pages
    the window — there is no free scrollbar, so rows are never half-height.

    ``height`` is the control's real front-panel bounds height (the panel passes
    it), so the visible-cell count falls out of the box the developer drew, just
    like the block-diagram glyph. Editing a cell writes it back; on a control
    (not an indicator) typing into the first past-the-end cell APPENDS, the way a
    LabVIEW array grows. Returns a refresh callable the Run handler calls after
    writing a new list into an indicator.
    """
    visible = max(1, int(height // _CELL_H))
    view = {"index": 0}  # top element index of the visible window

    def _values() -> list:
        return getattr(state, field) or []

    def _clamp(i: int) -> int:
        n = len(_values())
        return max(0, min(i, max(0, n - 1)))

    def _step(delta: int) -> None:
        view["index"] = _clamp(view["index"] + delta)
        index_box.value = view["index"]
        cells.refresh()

    def _set_index(value: Any) -> None:
        try:
            i = int(value)
        except (TypeError, ValueError):
            i = 0
        view["index"] = _clamp(i)
        cells.refresh()

    def _edit(elem_index: int, text: str) -> None:
        values = getattr(state, field)
        if elem_index < len(values):
            values[elem_index] = _coerce(text)
        elif elem_index == len(values) and text.strip() != "":
            values.append(_coerce(text))  # LabVIEW-style grow past the end
            cells.refresh()

    @ui.refreshable
    def cells() -> None:
        values = list(_values())
        n = len(values)
        with ui.column().classes("grow no-wrap gap-0 h-full"):
            for row in range(visible):
                ei = view["index"] + row
                past = ei >= n
                # The first past-the-end cell of a control is the LabVIEW "grow"
                # cell — editable, hinted; further past-end cells are inert.
                grow_cell = past and not readonly and ei == n
                with ui.row().classes("no-wrap items-center gap-0 w-full").style(
                    f"height:{_CELL_H}px"
                ):
                    cell = (
                        ui.input(value="" if past else str(values[ei]))
                        .props("dense borderless")
                        .classes("w-full text-xs")
                        .style(f"min-height:{_CELL_H}px")
                    )
                    if past:
                        cell.classes("opacity-40")  # greyed unset/past-end cell
                    if grow_cell:
                        cell.props('placeholder="+"')
                    if readonly or (past and not grow_cell):
                        cell.props("readonly")
                    else:
                        cell.on_value_change(
                            lambda e, i=ei: _edit(i, e.value)
                        )

    # Index control (left) + element viewport (right), filling the bounds box.
    with ui.column().classes("w-full h-full gap-0 no-wrap"):
        if label:
            ui.label(label).classes(
                "text-xs font-semibold text-gray-500 shrink-0 truncate leading-none"
            )
        with ui.row().classes("w-full grow no-wrap gap-0 items-stretch"):
            # Index control: tiny ▲/▼ carets (plain clickable labels so their
            # height is exact — Quasar buttons won't shrink into a short box) plus
            # a TYPEABLE index field, so you can step or jump to any index.
            with ui.column().classes(
                "shrink-0 no-wrap items-center justify-center gap-0"
            ).style(f"width:{_INDEX_W}px"):
                caret = "cursor-pointer leading-none text-gray-500 select-none"
                ui.label("▲").classes(f"text-[9px] {caret}").on(
                    "click", lambda: _step(1)
                )
                index_box = (
                    ui.number(value=0, min=0)
                    .props("dense borderless input-class=text-center")
                    .classes("text-xs")
                    .style(f"width:{_INDEX_W}px;min-height:18px")
                )
                index_box.on_value_change(lambda e: _set_index(e.value))
                ui.label("▼").classes(f"text-[9px] {caret}").on(
                    "click", lambda: _step(-1)
                )
            cells()
    return cells.refresh

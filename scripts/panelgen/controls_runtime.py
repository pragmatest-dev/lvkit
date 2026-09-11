"""Runtime controls for LabVIEW front-panel types, composed from NATIVE NiceGUI
elements (ui.column/ui.row/ui.input/ui.number/ui.button) — no custom Vue.

Copied verbatim into each generated panel dir as ``controls.py`` so a generated
panel stays self-contained (the gallery loads panels standalone). ``panel.py``
imports ``array_control`` from here for any array-typed control/indicator.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui


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
    state: Any, field: str, *, readonly: bool = False, label: str = ""
) -> Callable[[], None]:
    """Editable 1D array bound to ``state.<field>`` (a list), composed from
    native NiceGUI elements: one input per element, add/delete buttons.

    Like a LabVIEW array control, this is a FIXED-SIZE VIEWPORT: it fills its
    parent (which the panel sizes to the control's front-panel bounds) and the
    element rows SCROLL inside it — adding elements never grows the box past its
    bounds or overlaps neighboring controls. The parent must have a definite
    height (the panel emits ``height:<bounds>px;overflow:hidden`` on the wrapper).

    Returns a refresh callable — the panel calls it after the Run handler writes
    a new list into an indicator field, so the displayed rows update.
    """

    def _add() -> None:
        getattr(state, field).append(0)
        rows.refresh()

    def _delete(idx: int) -> None:
        del getattr(state, field)[idx]
        rows.refresh()

    @ui.refreshable
    def rows() -> None:
        values = list(getattr(state, field) or [])
        for i, val in enumerate(values):
            with ui.row().classes("items-center gap-1 no-wrap w-full"):
                ui.label(str(i)).classes(
                    "text-xs text-gray-400 w-5 text-right shrink-0"
                )
                inp = (
                    ui.input(value=str(val))
                    .props("dense borderless")
                    .classes("flex-grow text-xs")
                )
                if readonly:
                    inp.props("readonly")
                else:

                    def _on_change(e: Any, idx: int = i) -> None:
                        getattr(state, field)[idx] = _coerce(e.value)

                    inp.on_value_change(_on_change)
                    ui.button(
                        icon="close", on_click=lambda _e, idx=i: _delete(idx)
                    ).props("flat dense round size=xs").classes("shrink-0")
        if not values:
            ui.label("(empty)").classes("text-xs text-gray-400 italic")

    # Fill the (bounds-sized) parent. The label + add-icon share ONE thin header
    # row (fixed) so they don't eat the element viewport; only the rows region
    # scrolls (min-h-0 lets the flex child shrink so overflow-y-auto engages
    # instead of the column growing past its bounds).
    with ui.column().classes("w-full h-full gap-0 no-wrap"):
        with ui.row().classes("items-center gap-1 no-wrap w-full shrink-0"):
            if label:
                ui.label(label).classes(
                    "text-xs font-semibold text-gray-500 flex-grow truncate"
                )
            if not readonly:
                ui.button(icon="add", on_click=_add).props(
                    "flat dense round size=xs"
                ).classes("shrink-0")
        with ui.column().classes("w-full grow min-h-0 overflow-y-auto gap-0"):
            rows()
    return rows.refresh

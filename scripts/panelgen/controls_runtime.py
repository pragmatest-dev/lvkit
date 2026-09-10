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

    Returns a refresh callable — the panel calls it after the Run handler writes
    a new list into an indicator field, so the displayed rows update.
    """
    if label:
        ui.label(label).classes("text-xs font-semibold text-gray-500")

    def _add() -> None:
        getattr(state, field).append(0)
        rows.refresh()

    def _delete(idx: int) -> None:
        del getattr(state, field)[idx]
        rows.refresh()

    @ui.refreshable
    def rows() -> None:
        values = list(getattr(state, field) or [])
        with ui.column().classes("gap-1 w-full"):
            for i, val in enumerate(values):
                with ui.row().classes("items-center gap-1 no-wrap w-full"):
                    ui.label(str(i)).classes("text-xs text-gray-400 w-6 text-right")
                    inp = ui.input(value=str(val)).props("dense").classes("flex-grow")
                    if readonly:
                        inp.props("readonly")
                    else:

                        def _on_change(e: Any, idx: int = i) -> None:
                            getattr(state, field)[idx] = _coerce(e.value)

                        inp.on_value_change(_on_change)
                        ui.button(
                            icon="remove", on_click=lambda _e, idx=i: _delete(idx)
                        ).props("flat dense round")
            if not values:
                ui.label("(empty)").classes("text-xs text-gray-400 italic")
        if not readonly:
            ui.button("+ element", on_click=_add).props("flat dense").classes("text-xs")

    rows()
    return rows.refresh

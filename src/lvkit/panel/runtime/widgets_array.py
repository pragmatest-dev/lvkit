"""The widget of a one-dimensional array control and indicator: a grid with a row
for each element and the element index in front.

The value that crosses to the function is a list of the elements. An element is
a number, Boolean, string, path or enum/ring item for an array of one of those,
and, for an array of clusters, what ``widgets_cluster`` gives for a cluster (a
``types.SimpleNamespace`` of its fields for a cluster type definition, else a
tuple); a cluster's fields are the grid's columns. A complex number is two
columns, real and imaginary. A control has a button that
adds a row of default values and a column that removes a row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from .live import LiveValues
from .port import Member, Port, bind_control, bind_indicator
from .widgets import as_number, bool_port, complex_port, number_port, string_port
from .widgets_choice import choice_port
from .widgets_cluster import cluster_port
from .widgets_path import path_port, path_text

ROW_HEIGHT = 24
HEADER_HEIGHT = 24
# The grid fills its container, so the array has one width of its own: without
# it the grid shrinks to the width of its caption.
WIDTH_CLS = "w-80"

_REMOVE = (
    '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" '
    'stroke="currentColor" stroke-width="2"><path d="M3 3l10 10M13 3L3 13"/></svg>'
)


@dataclass
class _Column:
    """The column or columns of the grid that hold one value (a complex number
    has two) and how a row's cell values turn into the value and back. ``key`` is
    the field's attribute name in a row that is an object."""

    key: str
    definitions: list[dict[str, Any]]
    default: dict[str, Any]
    to_cells: Callable[[Any], dict[str, Any]]
    from_cells: Callable[[dict[str, Any]], Any]


def _base(member: Member) -> dict[str, Any]:
    return {
        "headerName": member.label,
        "field": member.key,
        "colId": member.key,
        "flex": 1,
        "editable": True,
    }


def _column(
    member: Member,
    definition: dict[str, Any],
    default: Any,
    to_cell: Callable[[Any], Any],
    from_cell: Callable[[Any], Any],
) -> _Column:
    """A value held in the one column named ``member.key``."""
    key = member.key
    return _Column(
        key,
        [definition],
        {key: default},
        lambda value: {key: to_cell(value)},
        lambda cells: from_cell(cells.get(key)),
    )


def _number_column(member: Member) -> _Column:
    default, integer = member.arguments
    definition = {**_base(member), "cellDataType": "number"}
    if integer:
        definition["cellEditor"] = "agNumberCellEditor"
        definition["cellEditorParams"] = {"precision": 0}
    return _column(
        member,
        definition,
        default,
        lambda value: value,
        lambda cell: as_number(cell, integer),
    )


def _complex_column(member: Member) -> _Column:
    (default,) = member.arguments
    real, imaginary = f"{member.key}_re", f"{member.key}_im"

    def definition(key: str, part: str) -> dict[str, Any]:
        header = " ".join(filter(None, [member.label, part]))
        return {
            **_base(member),
            "headerName": header,
            "field": key,
            "colId": key,
            "cellDataType": "number",
        }

    return _Column(
        member.key,
        [definition(real, "re"), definition(imaginary, "im")],
        {real: default.real, imaginary: default.imag},
        lambda value: {real: value.real, imaginary: value.imag},
        lambda cells: complex(cells.get(real) or 0, cells.get(imaginary) or 0),
    )


def _bool_column(member: Member) -> _Column:
    (default,) = member.arguments
    definition = {**_base(member), "cellDataType": "boolean"}
    return _column(member, definition, default, bool, lambda cell: bool(cell))


def _string_column(member: Member) -> _Column:
    (default,) = member.arguments
    definition = {**_base(member), "cellDataType": "text"}
    return _column(member, definition, default, str, lambda cell: str(cell or ""))


def _path_column(member: Member) -> _Column:
    (default,) = member.arguments
    definition = {**_base(member), "cellDataType": "text"}
    return _column(
        member, definition, default, path_text, lambda cell: Path(cell or "")
    )


def _choice_column(member: Member) -> _Column:
    items, default = member.arguments
    labels = {value: label for label, value in items}
    values = dict(items)
    first = items[0][1]
    definition = {
        **_base(member),
        "cellDataType": "text",
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": [label for label, _ in items]},
    }
    return _column(
        member,
        definition,
        labels.get(default, str(default)),
        lambda value: labels.get(value, str(value)),
        lambda cell: values.get(cell, first),
    )


_COLUMNS: dict[Callable[..., Port], Callable[[Member], _Column]] = {
    number_port: _number_column,
    complex_port: _complex_column,
    bool_port: _bool_column,
    string_port: _string_column,
    path_port: _path_column,
    choice_port: _choice_column,
}


def _columns(element: Member) -> tuple[list[_Column], str]:
    """The columns for an element, and how a row makes an element: ``scalar``,
    ``object`` or ``tuple``."""
    if element.builder is cluster_port:
        named, members = element.arguments
        return [_COLUMNS[m.builder](m) for m in members], (
            "object" if named else "tuple"
        )
    return [_COLUMNS[element.builder](element)], "scalar"


def _field(value: Any, column: _Column, position: int, form: str) -> Any:
    if form == "scalar":
        return value
    return getattr(value, column.key) if form == "object" else value[position]


def array_port(
    element: Member, rows: int, initial: list[Any], *, readonly: bool
) -> Port:
    columns, form = _columns(element)
    cells: list[dict[str, Any]] = []
    headers = form != "scalar" or len(columns[0].definitions) > 1
    index = {
        "headerName": "",
        "valueGetter": "node.rowIndex",
        "width": 30,
        "editable": False,
        "pinned": "left",
        "cellClass": "text-gray-400 text-right",
        "cellStyle": {"padding": "0 4px"},
    }
    remove = {
        "colId": "remove",
        "headerName": "",
        "width": 24,
        "editable": False,
        "pinned": "right",
        ":cellRenderer": f"() => `{_REMOVE}`",
        "cellClass": "cursor-pointer text-center text-gray-400",
        "cellStyle": {"padding": "0"},
    }
    options = {
        "columnDefs": [
            index,
            *[d for c in columns for d in c.definitions],
            *([] if readonly else [remove]),
        ],
        "rowData": cells,
        "rowHeight": ROW_HEIGHT,
        "headerHeight": HEADER_HEIGHT if headers else 0,
        "suppressNoRowsOverlay": True,
        "suppressHorizontalScroll": True,
        "suppressCellFocus": readonly,
    }
    port: Port
    with ui.column().classes(f"{WIDTH_CLS} gap-1"):
        if not readonly:
            add = ui.button(icon="add", on_click=lambda: add_row())
            add.props("flat dense round size=xs").classes("text-gray-500")
            add.mark("add-row")
        height = rows * ROW_HEIGHT + 4 + (HEADER_HEIGHT if headers else 0)
        grid = ui.aggrid(options).style(f"height:{height}px").classes("w-full")

    def to_element(cell: dict[str, Any]) -> Any:
        values = [c.from_cells(cell) for c in columns]
        if form == "scalar":
            return values[0]
        if form == "object":
            return SimpleNamespace(**{c.key: v for c, v in zip(columns, values)})
        return tuple(values)

    def to_cell(value: Any) -> dict[str, Any]:
        cell: dict[str, Any] = {}
        for position, c in enumerate(columns):
            cell |= c.to_cells(_field(value, c, position, form))
        return cell

    def refresh() -> None:
        grid.options["rowData"] = list(cells)
        grid.update()

    def get() -> list[Any]:
        return [to_element(cell) for cell in cells]

    def show(values: Any) -> None:
        cells[:] = [to_cell(value) for value in values or []]
        refresh()

    def add_row() -> None:
        cells.append({key: v for c in columns for key, v in c.default.items()})
        refresh()
        port.changed()

    def edited(event: Any) -> None:
        position = event.args.get("rowIndex")
        if isinstance(position, int) and 0 <= position < len(cells):
            cells[position] = event.args["data"]
            port.changed()

    def clicked(event: Any) -> None:
        position = event.args.get("rowIndex")
        if event.args.get("colId") == "remove" and isinstance(position, int):
            if 0 <= position < len(cells):
                del cells[position]
                refresh()
                port.changed()

    port = Port(get, show)
    if initial:
        show(initial)
    if not readonly:
        grid.on("cellValueChanged", edited)
        grid.on("cellClicked", clicked)
    return port


def array_control(
    live: LiveValues, name: Any, element: Member, rows: int, initial: list[Any]
) -> Port:
    return bind_control(live, name, array_port(element, rows, initial, readonly=False))


def array_indicator(
    live: LiveValues, name: Any, element: Member, rows: int, initial: list[Any]
) -> Port:
    return bind_indicator(live, name, array_port(element, rows, initial, readonly=True))

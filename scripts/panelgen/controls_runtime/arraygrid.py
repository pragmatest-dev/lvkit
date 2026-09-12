"""The LabVIEW array control/indicator analog, composed from AG Grid
(``ui.aggrid`` -- a maintained data grid; we own the MAPPING, not a bespoke
grid). Mirrors the way lvkit already draws a LabVIEW array
(``render/glyphs/nodes/array_constant.py``): an INDEX CONTROL on the left
(the element index, ``node.rowIndex``) and, to its right, a viewport showing a
WHOLE NUMBER of element cells -- ``floor(box_height / cell_h)`` -- starting at
the current index. It is index-driven, NOT a free scrollbar, so you never see
half-height rows; stepping the index pages the window through the array.
Typing into the first past-the-end (greyed) cell appends, the way a LabVIEW
array grows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import ui

from ._util import _jsonable
from .filepicker import _SVG_FOLDER, _FilePicker
from .theme import theme

# Fallback element-cell height, used only when the parsed part geometry is
# unavailable; normally this comes from the VI's front panel.
_CELL_H = 22

# These MUST match the generation-time strategies' claimed control_types
# (strategies.NumericStrategy.control_types / EnumStrategy.control_types). The
# runtime package is copied standalone into generated output and cannot import
# scripts/panelgen, so the sets are duplicated here on purpose; keep them in step
# (a numeric alias added to NumericStrategy only would silently render as text).
_NUMERIC_TYPES = ("stdNum", "stdNumeric")
_ENUM_TYPES = ("stdEnum", "stdRing")


@dataclass
class ArrayField:
    """One column of an array control. A scalar array has a single field keyed
    ``value``; an array OF CLUSTERS has one ArrayField per cluster field. The
    element type drives the AG Grid ``cellDataType``, so the grid stores each
    cell already typed -- there is no Python-side coercion, and the same code path
    serves scalars and clusters alike.

    ``fields`` makes a field itself a COLUMN GROUP: a cluster nested inside an
    array-of-clusters' element cluster becomes a group of its own child
    columns (via generation-time ``ClusterStrategy.emit_array_field``) rather
    than one flat text column, by the same composition that gives arrays and
    clusters their other nesting."""

    key: str  # dict key in the row + AG Grid column field
    header: str = ""  # column header (shown only for cluster columns)
    element_type: str = "stdNum"
    integer: bool = False
    num_min: float | None = None
    num_max: float | None = None
    values: list[str] | None = None  # enum/ring options (agSelectCellEditor)
    fields: list[ArrayField] | None = None  # nested cluster -> column group


def _field_default(f: ArrayField) -> Any:
    """The zero value for a new cell of this field's declared type."""
    if f.fields:
        return {sub.key: _field_default(sub) for sub in f.fields}
    if f.element_type in _NUMERIC_TYPES:
        return 0
    if f.element_type == "stdBool":
        return False
    if f.element_type in _ENUM_TYPES and f.values:
        return f.values[0]
    return ""


def _first_leaf_key(fields: list[ArrayField], prefix: str = "") -> str | None:
    """The DOTTED path to the first leaf field (for start-editing a new row)."""
    for f in fields:
        full = prefix + f.key
        if f.fields:
            k = _first_leaf_key(f.fields, full + ".")
            if k:
                return k
        else:
            return full
    return None


def _field_column(f: ArrayField, *, editable: bool, prefix: str = "") -> dict[str, Any]:
    """One AG Grid column def built from a field's DECLARED type -- number
    (int/float, with the VI's range/precision), boolean, or text -- or, when
    ``f.fields`` is set, a column GROUP of its children's own columns. A nested
    field's ``field``/``colId`` is the DOTTED path from the array element root
    (e.g. ``addr.street``), so AG Grid reads/writes the nested dict the row holds
    -- matching the codegen's nested ``list[dict]`` for a nested cluster. The
    declared ``cellDataType`` is what makes the grid deliver each edited cell
    already typed."""
    full = prefix + f.key
    if f.fields:
        return {
            "headerName": f.header,
            "children": _columns_for(f.fields, editable=editable, prefix=full + "."),
        }
    numeric = f.element_type in _NUMERIC_TYPES
    col: dict[str, Any] = {
        "headerName": f.header,
        "field": full,
        "colId": full,
        "flex": 1,
        "editable": editable,
        "cellClass": "font-mono" + (" text-right" if numeric else ""),
        "tooltipField": full,  # full value on hover when a cell is ellipsized
    }
    if numeric:
        col["cellDataType"] = "number"
        editor: dict[str, Any] = {}
        if f.num_min is not None:
            editor["min"] = f.num_min
        if f.num_max is not None:
            editor["max"] = f.num_max
        if f.integer:
            editor["precision"] = 0
        if editor:
            col["cellEditor"] = "agNumberCellEditor"
            col["cellEditorParams"] = editor
    elif f.element_type == "stdBool":
        col["cellDataType"] = "boolean"
    elif f.element_type in _ENUM_TYPES and f.values:
        # enum/ring -> a dropdown of its options, matching ui.select outside.
        col["cellDataType"] = "text"
        col["cellEditor"] = "agSelectCellEditor"
        col["cellEditorParams"] = {"values": list(f.values)}
    else:
        col["cellDataType"] = "text"
    return col


def _columns_for(
    fields: list[ArrayField], *, editable: bool, prefix: str = ""
) -> list[dict[str, Any]]:
    """The AG Grid columns for a field list, recursively: a group column for a
    nested cluster, a typed column for a leaf, and -- right after a PATH leaf -- a
    Browse column (so a path cell gets the same picker affordance at EVERY level,
    top-level or nested, its colId carrying the leaf's dotted path)."""
    out: list[dict[str, Any]] = []
    for f in fields:
        out.append(_field_column(f, editable=editable, prefix=prefix))
        if not f.fields and f.element_type == "stdPath" and editable:
            out.append(_browse_column(prefix + f.key))
    return out


def _index_column(*, readonly: bool) -> dict[str, Any]:
    """The pinned, read-only element-index column (``node.rowIndex``), with a
    drag handle (hidden until hover -- see the package's injected CSS) so a row
    can be reordered by its index cell."""
    return {
        "headerName": "",
        "valueGetter": "node.rowIndex",
        "width": 34,  # the handle hides until hover, so the index gets it all
        "editable": False,
        "pinned": "left",
        "sortable": False,
        "suppressMovable": True,
        "resizable": False,
        # Drag by the index cell to reorder elements (AG Grid managed drag).
        "rowDrag": not readonly,
        "cellClass": "text-gray-400 text-right font-mono px-1",
    }


def _browse_column(field_key: str) -> dict[str, Any]:
    """A path field's Browse glyph column: opens the SAME server-filesystem
    picker ``path_control`` uses, so a path cell has the same affordance in the
    grid as outside it. A ``:cellRenderer`` draws the glyph -- the same
    mechanism the delete column's glyph uses (see ``_del_column``), so both
    read the same way."""
    return {
        "colId": f"browse:{field_key}",
        "headerName": "",
        "width": 26,
        "editable": False,
        "sortable": False,
        "suppressMovable": True,
        "resizable": False,
        ":cellRenderer": f"() => `{_SVG_FOLDER}`",
        "cellClass": "lv-browse cursor-pointer select-none px-0",
    }


def _del_column() -> dict[str, Any]:
    """The clickable ✕ remove-row column (AG Grid has no native remove UI --
    this is the Community pattern). Hidden until the row is hovered (see the
    package's injected CSS). A ``:cellRenderer`` draws the glyph, the same
    mechanism ``_browse_column`` uses for its folder glyph."""
    return {
        "colId": "del",
        "headerName": "",
        "width": 18,
        "editable": False,
        "sortable": False,
        "suppressMovable": True,
        "resizable": False,
        "pinned": "right",
        ":cellRenderer": "() => '✕'",
        "cellClass": "lv-del text-center cursor-pointer select-none px-0",
    }


def array_control(
    state: Any,
    field: str,
    *,
    readonly: bool = False,
    label: str = "",
    cell_h: int = _CELL_H,
    visible: int = 2,
    element_type: str = "stdNum",
    integer: bool = True,
    num_min: float | None = None,
    num_max: float | None = None,
    enum_values: list[str] | None = None,
    fields: list[ArrayField] | None = None,
) -> Callable[[], None]:
    """1D array bound to ``state.<field>`` (a list), rendered with AG Grid
    (``ui.aggrid`` — a maintained data grid) configured from the VI's real
    control properties. We own the MAPPING, not a bespoke grid:

    - a pinned, read-only INDEX column (the element index, ``node.rowIndex``);
    - one typed column PER FIELD — a scalar array has a single ``value`` column;
      an ARRAY OF CLUSTERS passes ``fields`` (one ``ArrayField`` per cluster
      field) and gets one column each, ``state.<field>`` then being a list of
      dicts. Each column declares its ``cellDataType`` (number/boolean/text) from
      the VI's real type, so the grid delivers every edited cell already typed —
      no Python coercion, and clusters are just more columns, not a second path;
    - only ``visible`` rows show (``rowHeight`` = the VI's element-cell height);
      the rest scroll, the always-on vertical scrollbar acting as the index
      navigator — the way a LabVIEW array is paged by its index.

    ``cell_h``, ``visible`` and the field type(s)/range are READ FROM THE VI's
    front panel. Edits write straight back into ``state.<field>``. Returns a
    refresh callable the Run handler calls after writing a new list.
    """
    # One code path for both: a scalar array is a single unnamed 'value' field;
    # a cluster array supplies its fields. Rows are dicts either way.
    cluster = fields is not None
    cols: list[ArrayField] = fields if cluster else [
        ArrayField("value", "", element_type, integer, num_min, num_max,
                   values=enum_values)
    ]

    def _row_from(elem: Any) -> dict[str, Any]:
        # Build the AG Grid row as a NESTED dict mirroring the cluster shape (a
        # nested cluster field -> a nested dict), so a column group's dotted-path
        # child columns resolve. _jsonable coerces a non-JSON leaf (a Path, ...).
        def build(fs: list[ArrayField], src: Any) -> dict[str, Any]:
            src = src if isinstance(src, dict) else {}
            return {
                f.key: (build(f.fields, src.get(f.key)) if f.fields
                        else _jsonable(src.get(f.key)))
                for f in fs
            }
        return build(cols, elem) if cluster else {"value": _jsonable(elem)}

    def _elem_from(row: dict[str, Any]) -> Any:
        # AG Grid delivers each cell typed per its column's cellDataType and, for
        # dotted fields, nests it back into the row dict -- so read the nested row
        # verbatim (no re-parsing; verbatim is what keeps "123" a str in a str col).
        def read(fs: list[ArrayField], r: Any) -> dict[str, Any]:
            r = r if isinstance(r, dict) else {}
            return {
                f.key: (read(f.fields, r.get(f.key)) if f.fields else r.get(f.key))
                for f in fs
            }
        return read(cols, row) if cluster else row.get("value")

    def _rows() -> list:
        return [_row_from(e) for e in (getattr(state, field) or [])]

    show_headers = cluster and any(f.header for f in cols)
    # Columns (typed, with Browse columns interleaved after path leaves) built
    # recursively so a nested cluster field becomes a column group -- same rules
    # at every level.
    field_cols = _columns_for(cols, editable=not readonly)
    col_defs: list[dict[str, Any]] = [_index_column(readonly=readonly), *field_cols]
    if not readonly:
        col_defs.append(_del_column())

    header_h = 22 if show_headers else 0
    options: dict[str, Any] = {
        "columnDefs": col_defs,
        "rowData": _rows(),
        "rowHeight": cell_h,
        # A scalar array has no header; a cluster array shows one per field.
        "headerHeight": header_h,
        # Compact: no empty horizontal scroll track; the vertical scrollbar (the
        # index navigator) shows only when there are more elements than fit.
        "suppressHorizontalScroll": True,
        "suppressMovableColumns": True,
        "suppressCellFocus": readonly,
        "suppressNoRowsOverlay": True,  # empty reads as empty, not a banner
        "rowDragManaged": not readonly,  # drag reorders the rows
        "enableBrowserTooltips": True,  # native title tooltips for tooltipField
    }

    def _on_change(e: Any) -> None:
        a = e.args
        vals = getattr(state, field)
        i = a.get("rowIndex")
        if isinstance(i, int) and 0 <= i < len(vals):
            # AG Grid already delivers each cell typed per its column's declared
            # cellDataType (text -> str, number -> int/float, boolean -> bool), so
            # store the row verbatim. No Python-side re-parsing: that's what turned
            # "123" in a STRING array into int 123.
            vals[i] = _elem_from(a["data"])

    async def _on_click(e: Any) -> None:
        col = e.args.get("colId", "")
        i = e.args.get("rowIndex")
        vals = getattr(state, field)
        if not (isinstance(i, int) and 0 <= i < len(vals)):
            return
        if col == "del":
            del vals[i]
            refresh()
        elif col.startswith("browse:"):
            # Same picker as path_control -- open it for this cell's path, write
            # the pick back. The key is the field's DOTTED path (e.g. "addr.file"
            # for a path inside a nested cluster), so walk/create the nested dict.
            key = col.split(":", 1)[1]
            if cluster:
                *parents, leaf = key.split(".")
                d = vals[i]
                for p in parents:
                    d = d.setdefault(p, {})
                current = d.get(leaf)
            else:
                current = vals[i]
            picked = await _FilePicker(str(current or Path.home()))
            if picked:
                if cluster:
                    d[leaf] = picked
                else:
                    vals[i] = picked
                refresh()

    edit_key = _first_leaf_key(cols) or "value"

    def _start_edit(index: int) -> None:
        grid.run_grid_method("ensureIndexVisible", index)
        grid.run_grid_method(
            "startEditingCell", {"rowIndex": index, "colKey": edit_key}
        )

    def _add() -> None:
        # AG Grid has no native "add" UI; append a new element defaulted per its
        # field type(s) -- a dict for a cluster, a scalar otherwise -- then edit
        # its first cell. The rowData update and the edit-start are separate
        # client messages, so starting the edit in THIS tick races the re-render
        # (the new row may not exist yet, or the render wipes the editor). Defer
        # the edit-start a tick so the row is rendered first, then focus its cell.
        vals = getattr(state, field)
        if cluster:
            # _field_default recurses for a nested cluster field -> a nested dict.
            vals.append({f.key: _field_default(f) for f in cols})
        else:
            vals.append(_field_default(cols[0]))
        refresh()
        new_index = len(vals) - 1
        ui.timer(0.15, lambda: _start_edit(new_index), once=True)

    # Caption row: the control name + a "+" add button for editable arrays. The
    # grid shows `visible` rows and scrolls (the index navigator) only for more.
    with ui.column().classes("w-full h-full gap-0 no-wrap"):
        with ui.row().classes("w-full items-center gap-1 no-wrap shrink-0"):
            if label:
                ui.label(label).classes(
                    "text-xs font-semibold text-gray-500 truncate flex-grow "
                    "leading-none"
                )
            if not readonly:
                ui.button(icon="add", on_click=_add).props(
                    "flat dense round size=xs"
                ).classes("text-gray-500 min-w-0")
        t = theme()
        grid = (
            ui.aggrid(options)
            .classes(f"{t.grid_class} w-full shrink-0")
            # Height comes from the VI (visible rows x real cell height) plus the
            # column-header row for a cluster array; the look (base theme +
            # tokens) comes from the active Theme.
            .style(f"height:{visible * cell_h + 4 + header_h}px;{t.grid_vars}")
        )

    def refresh() -> None:
        grid.options["rowData"] = _rows()
        grid.update()

    async def _drag_end(_e: Any) -> None:
        # AG Grid (managed drag) already reordered the rows; read the new order
        # back and sync state to it (the rowDragEnd event carries no payload).
        data = await grid.get_client_data()
        getattr(state, field)[:] = [_elem_from(d) for d in data]
        # Re-render from state: the index column is virtual (node.rowIndex) and
        # AG Grid doesn't re-run its valueGetter after a managed move, so without
        # this the indices go stale (a moved "0" ends up beside the wrong row).
        refresh()

    if not readonly:
        grid.on("cellValueChanged", _on_change)
        grid.on("cellClicked", _on_click)
        grid.on("rowDragEnd", _drag_end)

    return refresh

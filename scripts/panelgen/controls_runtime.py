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

import asyncio
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicegui import ui

# Fallback element-cell height, used only when the parsed part geometry is
# unavailable; normally this comes from the VI's front panel.
_CELL_H = 22


# --------------------------------------------------------------------------
# Theme: ONE place that restyles the whole panel. Because we own the mapping
# (not the widgets), every control + the toolbar read from the active Theme, so
# a single switch re-skins everything — a modern look or a classic LabVIEW one —
# WITHOUT touching any behavior. Styling only: colors/fonts/density, never
# geometry or wiring.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Theme:
    """Styling knobs for a panel. ``grid_class`` picks the AG Grid base theme;
    ``grid_vars`` are its ``--ag-*`` design tokens; ``toolbar_classes`` styles the
    VI toolbar row."""

    name: str
    grid_class: str
    grid_vars: str
    toolbar_classes: str


MODERN = Theme(
    name="modern",
    grid_class="ag-theme-quartz",
    grid_vars=(
        "font-size:12px;--ag-grid-size:4px;--ag-cell-horizontal-padding:6px;"
        "--ag-row-hover-color:#eff6ff;--ag-border-color:#e5e7eb"
    ),
    toolbar_classes=(
        "w-full items-center gap-1 px-2 py-1 border-b bg-gray-50 "
        "dark:bg-neutral-800 dark:border-neutral-700"
    ),
)

# A nod to classic LabVIEW / Win-9x: compact, boxy, warm-grey, Tahoma.
CLASSIC = Theme(
    name="classic",
    grid_class="ag-theme-balham",
    grid_vars=(
        "font-size:11px;--ag-grid-size:3px;--ag-cell-horizontal-padding:4px;"
        "--ag-borders:solid 1px;--ag-border-color:#808080;"
        "--ag-background-color:#ece9d8;--ag-header-background-color:#d4d0c8;"
        "--ag-odd-row-background-color:#f5f4ec;--ag-row-hover-color:#e8e6d5;"
        "--ag-font-family:Tahoma,Geneva,sans-serif"
    ),
    toolbar_classes=(
        "w-full items-center gap-1 px-2 py-1 border-b "
        "bg-[#d4d0c8] border-[#808080]"
    ),
)

_ACTIVE_THEME = MODERN


def set_theme(theme: Theme) -> None:
    """Switch the active theme (call once, e.g. in app.py, before build_panel).
    Restyles every control + the toolbar; behavior is unaffected."""
    global _ACTIVE_THEME
    _ACTIVE_THEME = theme


def theme() -> Theme:
    return _ACTIVE_THEME

# The ✕ delete affordance is hidden until its row is hovered (uncluttered), grey
# then, red when you point at it. Registered at MODULE IMPORT (before ui.run) so
# it lands in the real <head> -- calling ui.add_css/ui.html from inside a page
# build runs after the head is sent, so the rule never applies.
ui.add_css(
    # ✕ delete: hidden until the row is hovered.
    ".lv-del{color:#9ca3af;opacity:0;transition:opacity .12s}"
    ".ag-row-hover .lv-del{opacity:.85}"
    ".lv-del:hover{color:#ef4444;opacity:1 !important}"
    # Drag handle: hidden (takes no space) until the row is hovered, and small
    # when shown -- so it never crowds the index digits.
    ".ag-drag-handle{display:none !important}"
    ".ag-row-hover .ag-drag-handle{display:inline-block !important;"
    "opacity:.5;transform:scale(.8)}"
    # Path-cell Browse glyph: centered folder icon, brighter on hover.
    ".lv-browse{display:flex;align-items:center;justify-content:center;"
    "opacity:.6;transition:opacity .12s}"
    ".ag-row-hover .lv-browse{opacity:.8}"
    ".lv-browse:hover{opacity:1}",
    shared=True,  # apply to every page (default shared=False needs a live client)
)




def _jsonable(value: Any) -> Any:
    """Coerce a value to something NiceGUI can JSON-serialize to a widget. JSON
    primitives pass through; anything else (a ``pathlib.Path``, a datetime, ...)
    becomes its ``str()``. This is the presentation boundary: the pure logic
    keeps its real types (it returns ``Path`` objects), and the widget shows text
    -- without it a ``Path`` reaches NiceGUI's socket serializer and the update
    fails silently outside the Run error boundary (nothing renders)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# Clean-room folder / file glyphs for the picker rows (inline SVG, our own
# shapes -- robust where an emoji font is absent, unlike 📁/📄).
_SVG_FOLDER = (
    '<svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-2px">'
    '<path d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3l1.2 1.4H13.5a1 1 0 0 1 1 1v7'
    'a1 1 0 0 1-1 1H2.5a1 1 0 0 1-1-1z" fill="#f6c344" stroke="#d9a520" '
    'stroke-width=".7"/></svg>'
)
_SVG_FILE = (
    '<svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-2px">'
    '<path d="M4 1.5h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.5a1 1 0 0 1 1-1z" '
    'fill="#e9edf2" stroke="#9aa5b1" stroke-width=".7"/>'
    '<path d="M9 1.5v3h3" fill="none" stroke="#9aa5b1" stroke-width=".7"/></svg>'
)


class _FilePicker(ui.dialog):
    """A minimal server-filesystem browser dialog (adapted from NiceGUI's
    local_file_picker example): a grid of the current directory's entries,
    double-click a folder to descend / ``..`` to go up, pick a file/folder with
    Select. ``submit(path)`` resolves the ``await``. Backs the path control's
    real Browse -- the LabVIEW path-browse analog, adopt-first."""

    def __init__(self, directory: str) -> None:
        super().__init__()
        start = Path(directory).expanduser()
        self._dir = start if start.is_dir() else Path.home()
        with self, ui.card().style("min-width:440px"):
            self._grid = (
                ui.aggrid(
                    {
                        "columnDefs": [{"field": "name", "headerName": ""}],
                        "rowSelection": "single",
                        "rowData": [],
                    },
                    html_columns=[0],
                )
                .classes("w-full h-64")
                .on("cellDoubleClicked", self._descend)
            )
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Cancel", on_click=self.close).props("flat")
                ui.button("Select", on_click=self._ok)
        self._refresh()

    def _refresh(self) -> None:
        try:
            entries = sorted(
                (p for p in self._dir.iterdir() if not p.name.startswith(".")),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            entries = []
        def _cell(icon: str, text: str) -> str:
            # Inline-flex keeps the glyph beside the name on one row (a bare SVG
            # + text wraps in an AG Grid html cell).
            return (
                '<span style="display:inline-flex;align-items:center;gap:6px">'
                f"{icon}<span>{text}</span></span>"
            )

        rows = [
            {"name": _cell(_SVG_FOLDER, ".."), "path": str(self._dir.parent)}
        ]
        for p in entries:
            icon = _SVG_FOLDER if p.is_dir() else _SVG_FILE
            rows.append({"name": _cell(icon, p.name), "path": str(p)})
        self._grid.options["rowData"] = rows
        self._grid.options["columnDefs"][0]["headerName"] = str(self._dir)
        self._grid.update()

    async def _descend(self, e: Any) -> None:
        p = Path(e.args["data"]["path"])
        if p.is_dir():
            self._dir = p
            self._refresh()
        else:
            self.submit(str(p))

    async def _ok(self) -> None:
        rows = await self._grid.get_selected_rows()
        self.submit(rows[0]["path"] if rows else str(self._dir))


def path_control(
    state: Any, field: str, *, readonly: bool = False, label: str = ""
) -> None:
    """A LabVIEW path control: an outlined text field bound to ``state.<field>``,
    with a real Browse (a server-filesystem picker dialog) on an input. A
    read-only indicator shows the path without a browse button."""
    inp = ui.input(label).props("outlined dense").classes("w-full")

    def _text(v: Any) -> str:
        return _jsonable(v) or ""

    # A path is often longer than the field; a tooltip shows it in full on hover
    # (hidden while empty so there's no blank bubble).
    with inp:
        tip = ui.tooltip().bind_text_from(state, field, backward=_text)
        tip.bind_visibility_from(state, field, backward=bool)

    if readonly:
        # A path indicator holds a Path (from the logic); show it as text so
        # NiceGUI can serialize it (see _jsonable).
        inp.bind_value_from(state, field, backward=_text)
        inp.disable()
        return
    inp.bind_value(state, field)

    async def _browse() -> None:
        picked = await _FilePicker(str(getattr(state, field) or Path.home()))
        if picked:
            setattr(state, field, picked)

    with inp.add_slot("append"):
        ui.button(icon="folder_open", on_click=_browse).props(
            "flat dense round size=sm"
        ).classes("text-gray-500")
        ui.tooltip("Browse…")


def waveform_indicator(
    state: Any, field: str, *, label: str = "", height: int = 160
) -> Callable[[], None]:
    """Read-only waveform / graph indicator: renders ``state.<field>`` (a list of
    y-values, or ``[x, y]`` pairs) as a line chart via ``ui.echart`` -- a
    maintained charting library (adopt-first; we own only the mapping). This is
    the LabVIEW waveform-graph analogue. Returns a refresh callable the Run
    handler calls after writing new samples.
    """

    def _data() -> list:
        vals = list(getattr(state, field) or [])
        if vals and isinstance(vals[0], list | tuple):
            return [list(pt) for pt in vals]  # already [x, y] pairs
        return [[i, v] for i, v in enumerate(vals)]  # y-only -> index on x

    if label:
        ui.label(label).classes(
            "text-xs font-semibold text-gray-500 shrink-0 truncate leading-none"
        )
    chart = (
        ui.echart(
            {
                "grid": {"left": 44, "right": 14, "top": 12, "bottom": 26},
                "xAxis": {"type": "value"},
                "yAxis": {"type": "value"},
                "series": [
                    {"type": "line", "showSymbol": False, "data": _data()}
                ],
                "animation": False,
            }
        )
        .classes("w-full")
        .style(f"height:{height}px")
    )

    def refresh() -> None:
        chart.options["series"][0]["data"] = _data()
        chart.update()

    return refresh


_NUMERIC_TYPES = ("stdNum", "stdNumeric")


@dataclass
class ArrayField:
    """One column of an array control. A scalar array has a single field keyed
    ``value``; an array OF CLUSTERS has one ArrayField per cluster field. The
    element type drives the AG Grid ``cellDataType``, so the grid stores each
    cell already typed -- there is no Python-side coercion, and the same code path
    serves scalars and clusters alike."""

    key: str  # dict key in the row + AG Grid column field
    header: str = ""  # column header (shown only for cluster columns)
    element_type: str = "stdNum"
    integer: bool = False
    num_min: float | None = None
    num_max: float | None = None
    values: list[str] | None = None  # enum/ring options (agSelectCellEditor)


_ENUM_TYPES = ("stdEnum", "stdRing")


def _field_default(f: ArrayField) -> Any:
    """The zero value for a new cell of this field's declared type."""
    if f.element_type in _NUMERIC_TYPES:
        return 0
    if f.element_type == "stdBool":
        return False
    if f.element_type in _ENUM_TYPES and f.values:
        return f.values[0]
    return ""


def _field_column(f: ArrayField, *, editable: bool) -> dict[str, Any]:
    """One AG Grid column def built from a field's DECLARED type -- number
    (int/float, with the VI's range/precision), boolean, or text. The declared
    ``cellDataType`` is what makes the grid deliver each edited cell already
    typed."""
    numeric = f.element_type in _NUMERIC_TYPES
    col: dict[str, Any] = {
        "headerName": f.header,
        "field": f.key,
        "flex": 1,
        "editable": editable,
        "cellClass": "font-mono" + (" text-right" if numeric else ""),
        "tooltipField": f.key,  # full value on hover when a cell is ellipsized
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
        # _jsonable: a cell may be a Path (path array) or other non-JSON type;
        # coerce to text so NiceGUI can serialize the rowData.
        if cluster:
            src = elem if isinstance(elem, dict) else {}
            return {f.key: _jsonable(src.get(f.key)) for f in cols}
        return {"value": _jsonable(elem)}

    def _elem_from(row: dict[str, Any]) -> Any:
        # AG Grid delivers each cell already typed per its column's cellDataType,
        # so read the row back verbatim -- no re-parsing.
        if cluster:
            return {f.key: row.get(f.key) for f in cols}
        return row.get("value")

    def _rows() -> list:
        return [_row_from(e) for e in (getattr(state, field) or [])]

    show_headers = cluster and any(f.header for f in cols)
    # One typed column per field; a path field also gets a Browse column that
    # opens the SAME server-filesystem picker path_control uses, so a path cell
    # has the same affordance in the grid as outside it.
    field_cols: list[dict[str, Any]] = []
    for f in cols:
        field_cols.append(_field_column(f, editable=not readonly))
        if f.element_type == "stdPath" and not readonly:
            field_cols.append(
                {
                    "colId": f"browse:{f.key}",
                    "headerName": "",
                    "width": 26,
                    "editable": False,
                    "sortable": False,
                    "suppressMovable": True,
                    "resizable": False,
                    ":cellRenderer": f"() => `{_SVG_FOLDER}`",
                    "cellClass": "lv-browse cursor-pointer select-none px-0",
                }
            )
    col_defs: list[dict[str, Any]] = [
        {
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
        },
        *field_cols,
    ]
    if not readonly:
        # AG Grid has no native remove UI, so a clickable ✕ action column (the
        # Community pattern). It's hidden until the row is hovered (see _CSS).
        col_defs.append(
            {
                "colId": "del",
                "headerName": "",
                "width": 18,
                "editable": False,
                "sortable": False,
                "suppressMovable": True,
                "resizable": False,
                "pinned": "right",
                "valueGetter": "'✕'",
                "cellClass": "lv-del text-center cursor-pointer select-none px-0",
            }
        )

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
            # the pick back into the row (dict field for a cluster, the scalar
            # otherwise).
            key = col.split(":", 1)[1]
            current = vals[i].get(key) if cluster else vals[i]
            picked = await _FilePicker(str(current or Path.home()))
            if picked:
                if cluster:
                    vals[i][key] = picked
                else:
                    vals[i] = picked
                refresh()

    def _start_edit(index: int) -> None:
        grid.run_grid_method("ensureIndexVisible", index)
        grid.run_grid_method(
            "startEditingCell", {"rowIndex": index, "colKey": cols[0].key}
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


# --------------------------------------------------------------------------
# VI toolbar / header — reproduces a LabVIEW VI window's execution toolbar.
# Clean-room glyphs (our own shapes; NEVER NI's noloc_env_*.gif artwork):
# Run = solid triangle, Run Continuously = looping arrows, Abort = red stop
# square (live only while running), Pause = two bars. Styleable via the theme
# classes below (a modern default; swap for a classic look later).
# --------------------------------------------------------------------------

_SVG_RUN = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<polygon points="4,3 13,8 4,13" fill="currentColor"/></svg>'
)
_SVG_RUNCONT = (
    '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" '
    'stroke="currentColor" stroke-width="1.6">'
    '<path d="M12.5 8a4.5 4.5 0 1 1-1.3-3.2"/>'
    '<path d="M12.6 2.2 12.2 5l-2.7-.6" fill="currentColor" stroke="none"/></svg>'
)
_SVG_ABORT = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor"/></svg>'
)
_SVG_PAUSE = (
    '<svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">'
    '<rect x="4.5" y="3" width="2.6" height="10"/>'
    '<rect x="8.9" y="3" width="2.6" height="10"/></svg>'
)

def _format_error(exc: BaseException) -> tuple[str, str]:
    """(one-line title, full traceback) for a caught exception."""
    title = f"{type(exc).__name__}: {exc}"
    detail = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return title, detail


class RunController:
    """Drives a panel's execution the LabVIEW way, off ONE ``compute`` callable
    (async: latch the controls, call the pure logic, write the outputs):

    - **Run** runs ``compute`` once.
    - **Run Continuously** runs ``compute`` repeatedly until aborted/paused.
    - **Pause** suspends/resumes a continuous run.
    - **Abort** stops a run immediately.

    It is also the ERROR BOUNDARY -- the analog of LabVIEW's error-out merge.
    We strip error clusters in favour of natural Python exceptions, so the pure
    logic just raises; the controller catches that at the Run boundary, stores it
    (``error``/``error_detail``), stops the run, and the toolbar presents it as an
    error banner (see ``toolbar``). A fresh Run clears it.

    (A state-machine/event-structure VI will hand a longer-lived ``compute`` that
    loops internally; the same Abort stops it.) ``running``/``paused`` drive the
    toolbar's enabled states via ``_refresh``; ``error`` drives the banner via
    ``_on_error`` (both set by ``toolbar``)."""

    def __init__(self, compute: Callable[[], Any], *, interval: float = 0.1):
        self._compute = compute
        self._interval = interval
        self.running = False
        self.paused = False
        self.error: str | None = None
        self.error_detail: str | None = None
        self._task: asyncio.Task | None = None
        self._refresh: Callable[[], None] = lambda: None
        self._on_error: Callable[[], None] = lambda: None

    def _set_error(self, exc: BaseException) -> None:
        self.error, self.error_detail = _format_error(exc)
        self._on_error()

    def clear_error(self) -> None:
        if self.error is not None:
            self.error = None
            self.error_detail = None
            self._on_error()

    async def run_once(self) -> None:
        if self.running:
            return
        self.clear_error()
        self.running = True
        self._refresh()
        try:
            await self._compute()
        except Exception as exc:  # noqa: BLE001 -- Run is the error boundary
            self._set_error(exc)
        finally:
            self.running = False
            self._refresh()

    def run_continuous(self) -> None:
        # LabVIEW semantics: run the VI to COMPLETION, then run it again, until
        # aborted -- an async loop on the event loop (each iteration awaits the
        # whole compute; iterations never overlap), NOT a fixed-interval timer.
        if self.running:
            return
        self.clear_error()
        self.running = True
        self.paused = False
        self._refresh()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while self.running:
                if self.paused:
                    await asyncio.sleep(0.05)  # idle; Pause/Abort still land
                    continue
                await self._compute()  # run to completion before the next run
                await asyncio.sleep(self._interval)  # breather + yield the loop
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- Run is the error boundary
            self._set_error(exc)
        finally:
            self.running = False
            self.paused = False
            self._task = None
            self._refresh()

    def pause(self) -> None:
        if not self.running:
            return
        self.paused = not self.paused  # the loop reads this each iteration
        self._refresh()

    def abort(self) -> None:
        # Ask the loop to stop; it exits after the current run (its finally clears
        # running/paused and refreshes). Refresh now too so the toolbar responds
        # instantly rather than waiting out the last iteration.
        self.running = False
        self.paused = False
        self._refresh()


def _tb_button(svg: str, tip: str, on_click: Callable, *, enabled: bool,
               color: str) -> None:
    btn = (
        ui.button(on_click=on_click)
        .props("flat dense round")
        .classes(f"{color} min-w-0")
    )
    with btn:
        ui.html(svg)
        ui.tooltip(tip)
    if not enabled:
        btn.props("disable")


def toolbar(controller: RunController) -> None:
    """Render the VI execution toolbar bound to ``controller``. Run / Run
    Continuously are enabled when idle; Abort / Pause only while running. Below
    the buttons, an error banner shows any exception the last Run raised (our
    Python-exception stand-in for LabVIEW's error out) with an expandable
    traceback; it clears on the next Run."""

    @ui.refreshable
    def bar() -> None:
        running = controller.running
        with ui.row().classes(theme().toolbar_classes):
            _tb_button(_SVG_RUN, "Run", controller.run_once,
                       enabled=not running, color="text-green-700")
            _tb_button(_SVG_RUNCONT, "Run Continuously", controller.run_continuous,
                       enabled=not running, color="text-green-700")
            _tb_button(_SVG_ABORT, "Abort", controller.abort,
                       enabled=running, color="text-red-600")
            _tb_button(_SVG_PAUSE, "Pause", controller.pause,
                       enabled=running, color="text-gray-600")

    @ui.refreshable
    def error_banner() -> None:
        if not controller.error:
            return
        with ui.row().classes(
            "w-full items-start gap-2 px-2 py-1 border-b bg-red-50 "
            "border-red-200 dark:bg-red-950 dark:border-red-900"
        ):
            ui.icon("error_outline").classes("text-red-600 mt-0.5 shrink-0")
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(controller.error).classes(
                    "text-red-700 dark:text-red-300 text-sm font-medium break-all"
                )
                if controller.error_detail:
                    with ui.expansion("traceback").classes("text-xs w-full"):
                        ui.code(controller.error_detail).classes(
                            "text-xs whitespace-pre-wrap w-full"
                        )
            ui.button(icon="close", on_click=controller.clear_error).props(
                "flat dense round size=xs"
            ).classes("text-red-500 shrink-0")

    controller._refresh = bar.refresh
    controller._on_error = error_banner.refresh
    bar()
    error_banner()

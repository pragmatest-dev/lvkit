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

# Fallback element-cell height, used only when the parsed part geometry is
# unavailable; normally this comes from the VI's front panel.
_CELL_H = 22


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
    cell_h: int = _CELL_H,
    visible: int = 2,
    element_type: str = "stdNum",
    integer: bool = True,
    num_min: float | None = None,
    num_max: float | None = None,
) -> Callable[[], None]:
    """1D array bound to ``state.<field>`` (a list), rendered with AG Grid
    (``ui.aggrid`` — a maintained data grid) configured from the VI's real
    control properties. We own the MAPPING, not a bespoke grid:

    - a pinned, read-only INDEX column (the element index, ``node.rowIndex``);
    - a typed VALUE column — numeric (``cellDataType='number'``, integer
      precision + data range from the VI's ``StdNumMin``/``StdNumMax``) or text —
      editable unless this is an indicator;
    - only ``visible`` rows show (``rowHeight`` = the VI's element-cell height);
      the rest scroll, the always-on vertical scrollbar acting as the index
      navigator — the way a LabVIEW array is paged by its index.

    ``cell_h``, ``visible``, ``element_type``, ``integer`` and the numeric range
    are READ FROM THE VI's front panel (the panel passes them from the parsed
    part geometry + properties). Edits write straight back into
    ``state.<field>``. Returns a refresh callable the Run handler calls after
    writing a new list into an indicator.
    """
    numeric = element_type in ("stdNum", "stdNumeric")

    def _rows() -> list:
        return [{"value": v} for v in (getattr(state, field) or [])]

    value_col: dict[str, Any] = {
        "headerName": "",
        "field": "value",
        "flex": 1,
        "editable": not readonly,
        "cellClass": "font-mono" + (" text-right" if numeric else ""),
    }
    if numeric:
        value_col["cellDataType"] = "number"
        editor: dict[str, Any] = {}
        if num_min is not None:
            editor["min"] = num_min
        if num_max is not None:
            editor["max"] = num_max
        if integer:
            editor["precision"] = 0
        if editor:
            value_col["cellEditor"] = "agNumberCellEditor"
            value_col["cellEditorParams"] = editor
    else:
        value_col["cellDataType"] = "text"

    col_defs: list[dict[str, Any]] = [
        {
            "headerName": "",
            "valueGetter": "node.rowIndex",
            "width": 30,  # just the index digits; the value gets the room
            "editable": False,
            "pinned": "left",
            "sortable": False,
            "suppressMovable": True,
            "resizable": False,
            "cellClass": "text-gray-400 text-right font-mono px-1",
        },
        value_col,
    ]
    if not readonly:
        # AG Grid has no native remove UI, so a clickable ✕ action column
        # (the Community pattern); cellClicked on it removes that element.
        col_defs.append(
            {
                "colId": "del",
                "headerName": "",
                "width": 22,
                "editable": False,
                "sortable": False,
                "suppressMovable": True,
                "resizable": False,
                "pinned": "right",
                "valueGetter": "'✕'",
                "cellClass": "text-red-400 text-center cursor-pointer select-none px-0",
            }
        )

    options: dict[str, Any] = {
        "columnDefs": col_defs,
        "rowData": _rows(),
        "rowHeight": cell_h,
        "headerHeight": 0,  # a LabVIEW array has no column header
        # Compact: no empty horizontal scroll track; the vertical scrollbar (the
        # index navigator) shows only when there are more elements than fit.
        "suppressHorizontalScroll": True,
        "suppressMovableColumns": True,
        "suppressCellFocus": readonly,
        "suppressNoRowsOverlay": True,  # empty reads as empty, not a banner
    }

    def _on_change(e: Any) -> None:
        a = e.args
        vals = getattr(state, field)
        i = a.get("rowIndex")
        if isinstance(i, int) and 0 <= i < len(vals):
            v = a["data"].get("value")
            vals[i] = _coerce(v) if isinstance(v, str) else v

    def _on_click(e: Any) -> None:
        if e.args.get("colId") != "del":
            return
        i = e.args.get("rowIndex")
        vals = getattr(state, field)
        if isinstance(i, int) and 0 <= i < len(vals):
            del vals[i]
            refresh()

    def _add() -> None:
        # AG Grid has no native "add" UI; append (numeric default 0 / empty
        # string) and drop straight into editing the new element.
        vals = getattr(state, field)
        vals.append(0 if numeric else "")
        refresh()
        grid.run_grid_method(
            "startEditingCell", {"rowIndex": len(vals) - 1, "colKey": "value"}
        )

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
        grid = (
            ui.aggrid(options)
            .classes("ag-theme-balham w-full shrink-0")
            .style(
                # Compact density so real values fit the VI's small box (the
                # balham default padding is generous). One place to retune.
                f"height:{visible * cell_h + 4}px;font-size:12px;"
                "--ag-cell-horizontal-padding:4px;--ag-grid-size:3px;"
                "--ag-borders:solid 1px"
            )
        )

    def refresh() -> None:
        grid.options["rowData"] = _rows()
        grid.update()

    if not readonly:
        grid.on("cellValueChanged", _on_change)
        grid.on("cellClicked", _on_click)

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

# Theme (modern default). One place to restyle the whole toolbar.
_TOOLBAR_CLASSES = (
    "w-full items-center gap-1 px-2 py-1 border-b bg-gray-50 "
    "dark:bg-neutral-800 dark:border-neutral-700"
)


class RunController:
    """Drives a panel's execution the LabVIEW way, off ONE ``compute`` callable
    (async: latch the controls, call the pure logic, write the outputs):

    - **Run** runs ``compute`` once.
    - **Run Continuously** runs ``compute`` repeatedly until aborted/paused.
    - **Pause** suspends/resumes a continuous run.
    - **Abort** stops a run immediately.

    (A state-machine/event-structure VI will hand a longer-lived ``compute`` that
    loops internally; the same Abort stops it.) ``running``/``paused`` drive the
    toolbar's enabled states via ``_refresh`` (set by ``toolbar``)."""

    def __init__(self, compute: Callable[[], Any], *, interval: float = 0.1):
        self._compute = compute
        self._interval = interval
        self.running = False
        self.paused = False
        self._timer: Any = None
        self._refresh: Callable[[], None] = lambda: None

    async def run_once(self) -> None:
        if self.running:
            return
        self.running = True
        self._refresh()
        try:
            await self._compute()
        finally:
            self.running = False
            self._refresh()

    def run_continuous(self) -> None:
        if self.running:
            return
        self.running = True
        self.paused = False
        self._refresh()
        self._timer = ui.timer(self._interval, self._compute)

    def pause(self) -> None:
        if not self.running or self._timer is None:
            return
        self.paused = not self.paused
        self._timer.active = not self.paused
        self._refresh()

    def abort(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
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
    Continuously are enabled when idle; Abort / Pause only while running."""

    @ui.refreshable
    def bar() -> None:
        running = controller.running
        with ui.row().classes(_TOOLBAR_CLASSES):
            _tb_button(_SVG_RUN, "Run", controller.run_once,
                       enabled=not running, color="text-green-700")
            _tb_button(_SVG_RUNCONT, "Run Continuously", controller.run_continuous,
                       enabled=not running, color="text-green-700")
            _tb_button(_SVG_ABORT, "Abort", controller.abort,
                       enabled=running, color="text-red-600")
            _tb_button(_SVG_PAUSE, "Pause", controller.pause,
                       enabled=running, color="text-gray-600")

    controller._refresh = bar.refresh
    bar()

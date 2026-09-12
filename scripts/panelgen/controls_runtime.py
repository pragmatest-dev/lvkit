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

import traceback
from collections.abc import Callable
from dataclasses import dataclass
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
    "opacity:.5;transform:scale(.8)}",
    shared=True,  # apply to every page (default shared=False needs a live client)
)


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
        value_col,
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
        "rowDragManaged": not readonly,  # drag reorders the rows
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
        t = theme()
        grid = (
            ui.aggrid(options)
            .classes(f"{t.grid_class} w-full shrink-0")
            # Height comes from the VI (visible rows x real cell height); the
            # look (base theme + tokens) comes from the active Theme.
            .style(f"height:{visible * cell_h + 4}px;{t.grid_vars}")
        )

    def refresh() -> None:
        grid.options["rowData"] = _rows()
        grid.update()

    async def _drag_end(_e: Any) -> None:
        # AG Grid (managed drag) already reordered the rows; read the new order
        # back and sync state to it (the rowDragEnd event carries no payload).
        data = await grid.get_client_data()
        getattr(state, field)[:] = [d["value"] for d in data]
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
        self._timer: Any = None
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
        if self.running:
            return
        self.clear_error()
        self.running = True
        self.paused = False
        self._refresh()
        self._timer = ui.timer(self._interval, self._tick)

    async def _tick(self) -> None:
        # One continuous iteration; a raised exception stops the loop and shows.
        try:
            await self._compute()
        except Exception as exc:  # noqa: BLE001 -- Run is the error boundary
            self._set_error(exc)
            self.abort()

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

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

# Fallback element-cell height / index-column width, used only when the parsed
# part geometry is unavailable; normally these come from the VI's front panel.
_CELL_H = 22
_INDEX_W = 44


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
    index_width: int = _INDEX_W,
    cell_h: int = _CELL_H,
    visible: int = 2,
    element_type: str = "stdNum",
) -> Callable[[], None]:
    """1D array bound to ``state.<field>`` (a list), drawn like a LabVIEW array
    control: an INDEX DISPLAY (stacked ▲/▼ spinner + a numeric index) on the left
    and a viewport of ``visible`` WHOLE element cells on the right, showing
    ``state.<field>[index : index + visible]``. Stepping the index pages the
    window — there is no free scrollbar, so rows are never half-height.

    ``index_width``, ``cell_h``, ``visible`` and ``element_type`` are READ FROM
    THE VI's front panel (the panel passes them from the parsed part geometry —
    the index display and element cell), so the control reproduces the box the
    developer actually drew instead of guessing. Editing a cell writes it back;
    on a control (not an indicator) typing into the first past-the-end cell
    APPENDS, the way a LabVIEW array grows. Returns a refresh callable the Run
    handler calls after writing a new list into an indicator.
    """
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
        # Snap the field back to the valid index so you can't sit on an
        # out-of-range index and read phantom "valid" values.
        index_box.value = view["index"]
        cells.refresh()

    def _edit(elem_index: int, text: str) -> None:
        values = getattr(state, field)
        if elem_index < len(values):
            values[elem_index] = _coerce(text)
        elif elem_index == len(values) and text.strip() != "":
            values.append(_coerce(text))  # LabVIEW-style grow past the end
            cells.refresh()

    numeric = element_type in ("stdNum", "stdNumeric")
    # Right-align numeric values (spreadsheet convention) via the inner input.
    input_align = "text-right" if numeric else "text-left"

    @ui.refreshable
    def cells() -> None:
        values = list(_values())
        n = len(values)
        with ui.column().classes("grow no-wrap gap-0 h-full"):
            for row in range(visible):
                ei = view["index"] + row
                past = ei >= n
                # The first past-the-end cell of a control is the LabVIEW "grow"
                # cell — editable; further past-end cells are empty (no element).
                grow_cell = past and not readonly and ei == n
                border = "" if row == visible - 1 else "border-b border-gray-200 "
                bg = ""
                if past and not grow_cell:
                    bg = "bg-gray-100 dark:bg-neutral-800 "  # clearly no element
                elif grow_cell:
                    bg = "bg-gray-50 dark:bg-neutral-800/60 "
                with ui.element("div").classes(
                    f"w-full flex items-center px-1 {border}{bg}"
                    "dark:border-neutral-700"
                ).style(f"height:{cell_h}px"):
                    if past and not grow_cell:
                        # Empty slot: read as empty, never as a valid value.
                        ui.label("").classes("w-full")
                        continue
                    cell = (
                        ui.input(value=str(values[ei]) if not past else "")
                        .props(
                            f"dense borderless input-class='font-mono {input_align}'"
                        )
                        .classes("w-full text-xs")
                        .style(f"min-height:{cell_h - 2}px")
                    )
                    if grow_cell:
                        cell.props('placeholder="＋"')
                    if readonly:
                        cell.props("readonly").classes("text-gray-500")
                    else:
                        cell.on_value_change(lambda e, i=ei: _edit(i, e.value))

    # Outer FRAME: index display (left) | element display (right), the array
    # shell. A border + dividers make cells countable and their boundaries clear.
    with ui.column().classes("w-full h-full gap-0 no-wrap"):
        if label:
            ui.label(label).classes(
                "text-xs font-semibold text-gray-500 shrink-0 truncate leading-none"
            )
        with ui.row().classes(
            "w-full grow no-wrap gap-0 items-stretch overflow-hidden "
            "border border-gray-300 rounded-sm bg-white "
            "dark:bg-neutral-900 dark:border-neutral-600"
        ):
            # Index DISPLAY (LEFT), visually distinct (recessed, bordered off):
            # a STACKED ▲/▼ spinner next to the current index number. (NI: "an
            # array shell includes an index display on the left, an element
            # display on the right".)
            with ui.element("div").classes(
                "shrink-0 flex items-center justify-center gap-px "
                "border-r border-gray-300 bg-gray-100 "
                "dark:bg-neutral-800 dark:border-neutral-600"
            ).style(f"width:{index_width}px"):
                spin = "cursor-pointer leading-none text-gray-600 select-none block"
                with ui.column().classes("no-wrap items-center gap-0 shrink-0"):
                    ui.label("▲").classes(f"text-[8px] {spin}").on(
                        "click", lambda: _step(1)
                    )
                    ui.label("▼").classes(f"text-[8px] {spin}").on(
                        "click", lambda: _step(-1)
                    )
                index_box = (
                    ui.number(value=0, min=0)
                    .props("dense borderless input-class='text-center font-mono'")
                    .classes("text-xs")
                    .style(f"width:{max(16, index_width - 16)}px;min-height:18px")
                )
                index_box.on_value_change(lambda e: _set_index(e.value))
            # Element DISPLAY (RIGHT): the stacked cells.
            cells()
    return cells.refresh


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

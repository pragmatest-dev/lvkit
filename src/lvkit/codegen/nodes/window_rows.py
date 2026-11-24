"""The rows of a Property Node of the VI class that are the front panel window:
Front Panel Window:State, Open, Is Frontmost, Panel Bounds, Origin, Monitor, Behavior,
Custom Title and Edit Mode On Open (Title and Window Bounds are ``front_panel_title``
and ``front_panel_bounds``, whose value is the function's attribute).

The window belongs to the VI's front panel (``Panel`` of ``panels.py``), which holds
its state and tells the views attached to it (``PanelView.window_changed``,
``window_property_changed``), so a row reads and writes it through
``panel_of(<function>)``, for the VI's own window and for the reference of another
VI alike:

- State (NI, ``vi/fpstate.html``): ``window_state`` (1 Standard, 2 Closed until the
  window is opened, 3 Hidden, 4 Minimized, 5 Maximized); a write is
  ``set_window_state``, an error for a window that is not open.
- Open (the older boolean property; LabVIEW Wiki, VI class/Front Panel Window.Open
  property: "Setting it to TRUE displays the window, and setting it to FALSE closes
  the window"): a read is whether the window is not Closed; TRUE is FP.Open with its
  defaults and FALSE is FP.Close.
- Is Frontmost (``vi/fpwinisfrontmost.html``): a read is ``frontmost``; "Writing TRUE
  to this value brings the front panel to the front ... Writing FALSE to this value
  has no effect".
- Panel Bounds (``vi/fpwinpanelbounds.html``), Origin (the scroll position of the
  panel: the Pane class's ``generic/gobject/pane/origin.html``, a cluster of the
  horizontal and vertical coordinates, (0, 0) for a panel nothing has scrolled),
  Monitor (``vi/fpmonitor.html``, 0 the primary monitor), Behavior
  (``vi/fpbehavior.html``, 1 Default) and Edit Mode On Open (``vi/editmode.html``):
  the last value written (``window_value``), else the file's: Panel Bounds is the
  rectangle the file saves for the window (``function.window_bounds``); the file
  stores no Origin, Monitor, Behavior or Edit Mode that lvkit decodes, so a read
  before a write is the default of each (Origin (0, 0), Monitor 0, Behavior 1, Edit
  Mode FALSE, a VI opened to run).
- Custom Title (``vi/fpwincustomtitle.html``): "Indicates whether the VI has a
  custom title string. Write FALSE to remove the custom title string": a read is
  whether ``function.window_title`` differs from the VI's name, and FALSE written
  sets it to that name.

On the VI's own window a write also tells ``live_io`` what the window must do
(``set_window_state``, ``bring_to_front``, ``open_panel`` / ``close_panel``,
``set_panel_bounds``, ``set_panel_origin``, ``set_window_monitor``,
``set_window_behavior``, ``set_window_title``) and a read of Is Frontmost asks it
(``is_frontmost``); Edit Mode On Open is a fact of how the VI opens, which no window
answers."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import (
    VI_EDIT_MODE_PROPERTY_CODE,
    VI_FP_BEHAVIOR_PROPERTY_CODE,
    VI_FP_CUSTOM_TITLE_PROPERTY_CODE,
    VI_FP_IS_FRONTMOST_PROPERTY_CODE,
    VI_FP_MONITOR_PROPERTY_CODE,
    VI_FP_OPEN_PROPERTY_CODE,
    VI_FP_ORIGIN_PROPERTY_CODE,
    VI_FP_PANEL_BOUNDS_PROPERTY_CODE,
    VI_FP_STATE_PROPERTY_CODE,
)
from lvkit.models import LVType, PropertyDef

from ..ast_utils import LIVE_IO, parse_expr, parse_stmt
from ..context import CodeGenContext
from ..panels_module import PANEL_CALL_IMPORTS, PANEL_OF_NAME
from . import front_panel_bounds, front_panel_title

_STATE_CLOSED = 2
_BOUNDS_ELEMENTS = 4
_ORIGIN_ELEMENTS = 2
_DEFAULT_NAME = '.vi_name.rsplit(":", 1)[-1]'


@dataclass(frozen=True)
class Target:
    """What a row acts on: ``function`` is the expression of the VI's function (its
    own, or the one a reference holds), ``own`` whether it is the window of the VI
    being generated (a write then also tells ``live_io``), ``lv_type`` the type of
    the row's value terminal."""

    function: str
    own: bool
    lv_type: LVType | None
    node: PrimitiveNode
    ctx: CodeGenContext

    @property
    def panel(self) -> str:
        return f"{PANEL_OF_NAME}({self.function})"


@dataclass(frozen=True)
class WindowRow:
    """One property of the window: its code, NI's name, how a read of it is
    written (an expression) and how a write of ``value`` is (statements, the
    ``live_io`` calls of the VI's own window last)."""

    code: int
    name: str
    read: Callable[[Target], ast.expr]
    write: Callable[[Target, str], list[ast.stmt]]


def _live(target: Target, call: str) -> list[ast.stmt]:
    """The ``live_io`` call of a write on the VI's own window."""
    return [parse_stmt(f"{LIVE_IO}.{call}")] if target.own else []


def _held_value(
    name: str, member: str, default: str, live_method: str | None
) -> WindowRow:
    """A row that holds a number or flag the panel keeps (``window_value``)."""
    code = _CODES[name]

    def read(target: Target) -> ast.expr:
        return parse_expr(f'{target.panel}.window_value("{member}", {default})')

    def write(target: Target, value: str) -> list[ast.stmt]:
        statements = [
            parse_stmt(f'{target.panel}.set_window_value("{member}", {value})')
        ]
        if live_method is not None:
            statements += _live(target, f"{live_method}({value})")
        return statements

    return WindowRow(code, name, read, write)


def _cluster_value(
    name: str, member: str, default: str, count: int, live_method: str
) -> WindowRow:
    """A row that holds a cluster of ``count`` numbers as a tuple; ``default`` is
    the expression of a read before any write, with ``{function}`` the VI's
    function."""
    code = _CODES[name]

    def read(target: Target) -> ast.expr:
        held = default.format(function=target.function)
        return front_panel_bounds.cluster_read(
            f'{target.panel}.window_value("{member}", {held})',
            target.lv_type,
            target.node,
            target.ctx,
            name,
            count,
        )

    def write(target: Target, value: str) -> list[ast.stmt]:
        statements, elements = front_panel_bounds.cluster_tuple(
            value, target.lv_type, target.node, name, count
        )
        statements.append(
            parse_stmt(f'{target.panel}.set_window_value("{member}", {elements})')
        )
        statements += _live(target, f"{live_method}({elements})")
        return statements

    return WindowRow(code, name, read, write)


_CODES = {
    "State": VI_FP_STATE_PROPERTY_CODE,
    "Open": VI_FP_OPEN_PROPERTY_CODE,
    "Is Frontmost": VI_FP_IS_FRONTMOST_PROPERTY_CODE,
    "Panel Bounds": VI_FP_PANEL_BOUNDS_PROPERTY_CODE,
    "Origin": VI_FP_ORIGIN_PROPERTY_CODE,
    "Monitor": VI_FP_MONITOR_PROPERTY_CODE,
    "Behavior": VI_FP_BEHAVIOR_PROPERTY_CODE,
    "Custom Title": VI_FP_CUSTOM_TITLE_PROPERTY_CODE,
    "Edit Mode On Open": VI_EDIT_MODE_PROPERTY_CODE,
}


def _read_state(target: Target) -> ast.expr:
    return parse_expr(f"{target.panel}.window_state")


def _write_state(target: Target, value: str) -> list[ast.stmt]:
    return [
        parse_stmt(f"{target.panel}.set_window_state({value})"),
        *_live(target, f"set_window_state({value})"),
    ]


def _read_open(target: Target) -> ast.expr:
    return parse_expr(f"{target.panel}.window_state != {_STATE_CLOSED}")


def _write_open(target: Target, value: str) -> list[ast.stmt]:
    opened = [parse_stmt(f"{target.panel}.open_window()")]
    closed = [parse_stmt(f"{target.panel}.close_window()")]
    if target.own:
        opened.append(parse_stmt(f"{LIVE_IO}.open_panel()"))
        closed.append(parse_stmt(f"{LIVE_IO}.close_panel()"))
    return [
        ast.If(
            test=parse_expr(value),
            body=opened,
            orelse=closed,
        )
    ]


def _read_frontmost(target: Target) -> ast.expr:
    held = f"{target.panel}.frontmost"
    return parse_expr(f"{LIVE_IO}.is_frontmost({held})" if target.own else held)


def _write_frontmost(target: Target, value: str) -> list[ast.stmt]:
    return [
        ast.If(
            test=parse_expr(value),
            body=[
                parse_stmt(f"{target.panel}.bring_to_front()"),
                *_live(target, "bring_to_front()"),
            ],
            orelse=[],
        )
    ]


def _read_custom_title(target: Target) -> ast.expr:
    function = target.function
    return parse_expr(
        f"{function}.{front_panel_title.TITLE_ATTRIBUTE} != {function}{_DEFAULT_NAME}"
    )


def _write_custom_title(target: Target, value: str) -> list[ast.stmt]:
    function = target.function
    attribute = f"{function}.{front_panel_title.TITLE_ATTRIBUTE}"
    removed = [parse_stmt(f"{attribute} = {function}{_DEFAULT_NAME}")]
    removed += _live(target, f"set_window_title({attribute})")
    return [ast.If(test=parse_expr(f"not {value}"), body=removed, orelse=[])]


ROWS: tuple[WindowRow, ...] = (
    WindowRow(_CODES["State"], "State", _read_state, _write_state),
    WindowRow(_CODES["Open"], "Open", _read_open, _write_open),
    WindowRow(
        _CODES["Is Frontmost"], "Is Frontmost", _read_frontmost, _write_frontmost
    ),
    _cluster_value(
        "Panel Bounds",
        "panel_bounds",
        'getattr({function}, "window_bounds", None)',
        _BOUNDS_ELEMENTS,
        "set_panel_bounds",
    ),
    _cluster_value("Origin", "origin", "(0, 0)", _ORIGIN_ELEMENTS, "set_panel_origin"),
    _held_value("Monitor", "monitor", "0", "set_window_monitor"),
    _held_value("Behavior", "behavior", "1", "set_window_behavior"),
    WindowRow(
        _CODES["Custom Title"],
        "Custom Title",
        _read_custom_title,
        _write_custom_title,
    ),
    _held_value("Edit Mode On Open", "edit_mode_on_open", "False", None),
)

_BY_CODE = {row.code: row for row in ROWS}


def window_row(prop: PropertyDef) -> WindowRow | None:
    """The row a Property Node row is, by its code; None for any other row."""
    if len(prop.segments) != 1:
        return None
    return _BY_CODE.get(prop.segments[0].code)


def read(row: WindowRow, target: Target) -> ast.expr:
    """The expression that reads ``row``; the panel's imports are added."""
    target.ctx.import_panels(PANEL_CALL_IMPORTS)
    return row.read(target)


def write(row: WindowRow, target: Target, value: str) -> list[ast.stmt]:
    """The statements that write ``value`` to ``row``; the panel's imports are
    added."""
    target.ctx.import_panels(PANEL_CALL_IMPORTS)
    return row.write(target, value)

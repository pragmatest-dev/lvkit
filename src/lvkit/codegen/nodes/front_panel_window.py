"""FP.Open, FP.Close, FP.Center and Abort VI, the methods of the VI class that open,
close and center a front panel window and abort a VI.

The Invoke Node's reference is unwired (the default VI object, the VI holding the
node), wired from "This VI" (``InMemoryVIGraph.acts_on_own_front_panel_window``), or
the reference of another VI. The inputs are NI's (docs-be
``properties-and-methods/vi-server/vi/openfrontpanel.html``): Activate? (default
FALSE) and State (default 1, Standard); FP.Close, FP.Center (NI: "Specifies
whether to automatically center the front panel window on the computer screen") and
Abort VI have none.

On the VI's own front panel the function opens, closes or centers its own window
through ``live_io``, the way it shows a dialog box: ``live_io.open_panel(activate,
state)``, ``live_io.close_panel()`` and ``live_io.center_panel()``. A function called
without a ``live_io`` that shows windows does nothing and goes on; the panel's own
``live_io`` is the base class's here (``panel/live_io.py``). FP.Open and FP.Close
also keep the window's state on the VI's panel (``panel_of(function).open_window``,
``close_window``), so a read of Front Panel Window:State follows them, and tell the
views attached to the panel.

On the reference of another VI FP.Open and FP.Close are ``panel_of(reference)
.open_window(activate, state)`` and ``.close_window()``: the panel records the state
and tells its views (``PanelView.window_changed``), and with none attached the
program goes on. An invalid State (0 Invalid, 2 Closed) is NI's error ("the method
returns an error"), the node's error out. Abort VI is ``panel_of(reference).abort()``
(``Panel.abort``): NI's "Aborts the execution of a top-level VI. This method returns
error 1000 if you call it on a subVI"; on the default VI object it is the VI's own
panel. FP.Center does not change ``function.window_bounds`` (``front_panel_bounds``):
NI defines the centering by the computer screen, whose size a function with no window
does not have, so the saved bounds stay until the window's own ``live_io`` sets them.
FP.Center on the reference of another VI does nothing: that window belongs to its own
run, whose ``live_io`` the caller does not hold (``generate_on_reference``)."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import (
    VI_ABORT_METHOD_CODE,
    VI_FP_CENTER_METHOD_CODE,
    VI_FP_CLOSE_METHOD_CODE,
    VI_FP_OPEN_METHOD_CODE,
)

from ..ast_utils import LIVE_IO, parse_stmt
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panels_module import PANEL_CALL_IMPORTS, PANEL_OF_NAME
from . import ctrl_val
from .ctrl_val import report_panel_error

# The Invoke Node's own terminals: the VI reference in and out, then NI's
# parameters in order (Activate?, State), the way Run VI's Wait Until Done is 6.
_ACTIVATE = 6
_STATE = 8
_DEFAULT_ACTIVATE = "False"
_DEFAULT_STATE = "1"


def _input(node: PrimitiveNode, ctx: CodeGenContext, index: int, default: str) -> str:
    terminal = ctrl_val.terminal_at(node, "input", index)
    if terminal is None or not ctx.is_wired(terminal.id):
        return default
    return ctx.wired_input(terminal.id, node) or default


def _reference(node: PrimitiveNode, ctx: CodeGenContext) -> str:
    """The reference given, else the VI's own reference."""
    reference_in = ctrl_val.terminal_at(node, "input", ctrl_val.REFERENCE_IN)
    wired = (
        ctx.wired_input(reference_in.id, node)
        if reference_in is not None and ctx.is_wired(reference_in.id)
        else None
    )
    return wired or ctx.this_vi_expression()


def _reference_out(node: PrimitiveNode, ctx: CodeGenContext) -> dict[str, str]:
    """The reference out is the reference given, else the VI's own reference."""
    reference_out = ctrl_val.terminal_at(node, "output", ctrl_val.REFERENCE_OUT)
    if reference_out is not None and ctx.is_wired(reference_out.id):
        return {reference_out.id: _reference(node, ctx)}
    return {}


def _panel_call(node: PrimitiveNode, ctx: CodeGenContext, reference: str) -> str | None:
    """The method of ``panel_of(reference)`` the node calls, None for FP.Center."""
    if node.method_code == VI_FP_OPEN_METHOD_CODE:
        activate = _input(node, ctx, _ACTIVATE, _DEFAULT_ACTIVATE)
        state = _input(node, ctx, _STATE, _DEFAULT_STATE)
        return f"{PANEL_OF_NAME}({reference}).open_window({activate}, {state})"
    if node.method_code == VI_FP_CLOSE_METHOD_CODE:
        return f"{PANEL_OF_NAME}({reference}).close_window()"
    if node.method_code == VI_ABORT_METHOD_CODE:
        return f"{PANEL_OF_NAME}({reference}).abort()"
    return None


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The panel call and the ``live_io`` call; the reference out is the VI's own
    reference."""
    reference = ctx.this_vi_expression()
    statements = []
    if node.method_code == VI_FP_OPEN_METHOD_CODE:
        activate = _input(node, ctx, _ACTIVATE, _DEFAULT_ACTIVATE)
        state = _input(node, ctx, _STATE, _DEFAULT_STATE)
        call = f"{LIVE_IO}.open_panel({activate}, {state})"
    elif node.method_code == VI_FP_CENTER_METHOD_CODE:
        call = f"{LIVE_IO}.center_panel()"
    else:
        call = f"{LIVE_IO}.close_panel()"
    panel_call = _panel_call(node, ctx, reference)
    if panel_call is not None:
        statements.append(parse_stmt(panel_call))
    statements.append(parse_stmt(call))
    bindings = _reference_out(node, ctx)
    if panel_call is None:
        return CodeFragment(statements=statements, bindings=bindings)
    ctx.import_panels(PANEL_CALL_IMPORTS)
    return report_panel_error(node, ctx, statements, bindings)


def generate_on_reference(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """FP.Open, FP.Close and Abort VI on the reference of another VI (or, Abort VI,
    on the default VI object): the call on that VI's panel. FP.Center does nothing in
    this function; the reference out is the reference given."""
    call = _panel_call(node, ctx, _reference(node, ctx))
    bindings = _reference_out(node, ctx)
    if call is None:
        return CodeFragment(statements=[], bindings=bindings)
    ctx.import_panels(PANEL_CALL_IMPORTS)
    body: ast.stmt = parse_stmt(call)
    return report_panel_error(node, ctx, [body], bindings)

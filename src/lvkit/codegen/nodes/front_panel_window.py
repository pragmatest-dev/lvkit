"""FP.Open, FP.Close and FP.Center, the methods of the VI class that open, close and
center a front panel window, on the VI's own front panel.

The Invoke Node's reference is unwired (the default VI object, the VI holding the
node) or wired from "This VI" (``InMemoryVIGraph.acts_on_own_front_panel_window``).
The generated function opens, closes or centers its own window through ``live_io``,
the way it shows a dialog box: ``live_io.open_panel(activate, state)``,
``live_io.close_panel()`` and ``live_io.center_panel()``. A function called without a
``live_io`` that shows windows does nothing and goes on; the panel's own ``live_io``
is the base class's here (``panel/live_io.py``). The inputs are NI's (docs-be
``properties-and-methods/vi-server/vi/openfrontpanel.html``): Activate? (default
FALSE) and State (default 1, Standard); FP.Close and FP.Center (NI: "Specifies
whether to automatically center the front panel window on the computer screen") have
none.

FP.Center does not change ``function.window_bounds`` (``front_panel_bounds``): NI
defines the centering by the computer screen, whose size a function with no window
does not have, so the saved bounds stay until the window's own ``live_io`` sets
them. FP.Center on the reference of another VI does nothing either way: that window
belongs to its own run, whose ``live_io`` the caller does not hold
(``generate_on_reference``). FP.Open or FP.Close on the reference of another VI
stays a gap (``invoke_node``)."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_FP_CENTER_METHOD_CODE, VI_FP_OPEN_METHOD_CODE

from ..ast_utils import LIVE_IO, parse_stmt
from ..context import CodeGenContext
from ..fragment import CodeFragment
from . import ctrl_val

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


def _reference_out(node: PrimitiveNode, ctx: CodeGenContext) -> dict[str, str]:
    """The reference out is the reference given, else the VI's own reference."""
    bindings: dict[str, str] = {}
    reference_out = ctrl_val.terminal_at(node, "output", ctrl_val.REFERENCE_OUT)
    if reference_out is not None and ctx.is_wired(reference_out.id):
        reference_in = ctrl_val.terminal_at(node, "input", ctrl_val.REFERENCE_IN)
        wired = (
            ctx.wired_input(reference_in.id, node)
            if reference_in is not None and ctx.is_wired(reference_in.id)
            else None
        )
        bindings[reference_out.id] = wired or ctx.this_vi_expression()
    return bindings


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The ``live_io`` call; the reference out is the VI's own reference."""
    if node.method_code == VI_FP_OPEN_METHOD_CODE:
        activate = _input(node, ctx, _ACTIVATE, _DEFAULT_ACTIVATE)
        state = _input(node, ctx, _STATE, _DEFAULT_STATE)
        call = f"{LIVE_IO}.open_panel({activate}, {state})"
    elif node.method_code == VI_FP_CENTER_METHOD_CODE:
        call = f"{LIVE_IO}.center_panel()"
    else:
        call = f"{LIVE_IO}.close_panel()"
    return CodeFragment(
        statements=[parse_stmt(call)], bindings=_reference_out(node, ctx)
    )


def generate_on_reference(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """FP.Center on the reference of another VI: nothing to do in this function;
    the reference out is the reference given."""
    return CodeFragment(statements=[], bindings=_reference_out(node, ctx))

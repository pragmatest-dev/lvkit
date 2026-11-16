"""FP.Open and FP.Close, the methods of the VI class that open and close a front
panel window, on the VI's own front panel.

The Invoke Node's reference is unwired (the default VI object, the VI holding the
node) or wired from "This VI" (``InMemoryVIGraph.acts_on_own_front_panel_window``).
The generated function opens or closes its own window through ``live_io``, the
way it shows a dialog box: ``live_io.open_panel(activate, state)`` and
``live_io.close_panel()``. A function called without a ``live_io`` that shows
windows does nothing and goes on; the panel's own ``live_io`` is the base class's
here (``panel/live_io.py``). The inputs are NI's (docs-be
``properties-and-methods/vi-server/vi/openfrontpanel.html``): Activate? (default
FALSE) and State (default 1, Standard); FP.Close has none.

FP.Open or FP.Close on the reference of another VI stays a gap (``invoke_node``)."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_FP_OPEN_METHOD_CODE

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


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The ``live_io`` call; the reference out is the VI's own reference."""
    if node.method_code == VI_FP_OPEN_METHOD_CODE:
        activate = _input(node, ctx, _ACTIVATE, _DEFAULT_ACTIVATE)
        state = _input(node, ctx, _STATE, _DEFAULT_STATE)
        call = f"{LIVE_IO}.open_panel({activate}, {state})"
    else:
        call = f"{LIVE_IO}.close_panel()"
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
    return CodeFragment(statements=[parse_stmt(call)], bindings=bindings)

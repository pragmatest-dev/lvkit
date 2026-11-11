"""The front panel of a VI reached through a VI reference: the methods Ctrl Val.Set,
Ctrl Val.Get and Ctrl Val.Get All, and Run VI on a reference the code holds.

A VI's front panel holds the value of each control and indicator between calls,
which a Python function does not. The generated package gets a ``front_panels``
module (``codegen/front_panels_module.py``) whose ``FrontPanel`` is that front
panel, one per function:

- Ctrl Val.Set is ``FrontPanel.of(reference).set(name, value)``: "Sets the value
  of a named control or indicator of the variant" (NI:
  properties-and-methods/vi-server/vi/setctrlvalvariant.html);
- Ctrl Val.Get is ``.get(name)``: "Gets the value of a named control or indicator
  as variant data";
- Ctrl Val.Get All is ``.get_all(controls)``: "an array of clusters that contains
  control and indicator names and their values as variant data", every control
  when its Controls input is TRUE and, FALSE by default, every indicator;
- Run VI is ``.run(wait)``: it "uses the current values of all front panel
  controls for execution rather than using data passed in through parameters".

A name the VI has no control or indicator of is error 1100 (NI, General LabVIEW
Error Codes: "No object of that name was found."), and a reference that is Not a
Refnum is error 1026, both reported on the Invoke Node's own error out
(``error_flow``). The deprecated forms of these methods take the value as
flattened data and a type string, which has no Python meaning: they stay a gap.
"""

from __future__ import annotations

import ast
from typing import cast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_CLASS
from lvkit.models import Terminal

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..error_flow import assigned_names, output_defaults
from ..fragment import CodeFragment
from ..front_panels_module import FRONT_PANEL_ERROR_NAME, FRONT_PANEL_NAME

CTRL_VAL_SET_CODE = 1051
CTRL_VAL_GET_CODE = 1052
CTRL_VAL_GET_ALL_CODE = 1053
_CODES = (CTRL_VAL_SET_CODE, CTRL_VAL_GET_CODE, CTRL_VAL_GET_ALL_CODE)
# The method codes the corpus records for Ctrl Val.Set (Deprecated) and Ctrl
# Val.Get All (Deprecated).
_DEPRECATED_CODES = (1009, 1020)

# The Invoke Node's terminals: the VI reference in and out, then the method's
# parameters from index 5 (a result at 5, its parameters at 6 and 8).
REFERENCE_IN = 0
REFERENCE_OUT = 1
_RESULT = 5
_NAME = 6
_VALUE = 8
_CONTROLS = 6


def is_ctrl_val(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node calling Ctrl Val.Set, Get or Get All on
    a VI reference."""
    return node.object_name == VI_CLASS and node.method_code in _CODES


def is_deprecated_ctrl_val(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node calling a deprecated form of Ctrl Val.Set
    or Get All, which moves the value as flattened bytes and a type string."""
    return node.object_name == VI_CLASS and node.method_code in _DEPRECATED_CODES


def terminal_at(node: PrimitiveNode, direction: str, index: int) -> Terminal | None:
    """The terminal of ``node`` at ``index`` in ``direction``."""
    return next(
        (t for t in node.terminals if t.direction == direction and t.index == index),
        None,
    )


def reference_value(node: PrimitiveNode, ctx: CodeGenContext) -> str:
    """The expression of the VI reference wired into ``node``."""
    reference = terminal_at(node, "input", REFERENCE_IN)
    value = (
        ctx.wired_input(reference.id, node)
        if reference is not None and ctx.is_wired(reference.id)
        else None
    )
    if value is None:
        raise ConversionGap(
            f"{node.method_name}: the VI reference input is not wired.", node
        )
    return value


def _input_value(
    node: PrimitiveNode, ctx: CodeGenContext, index: int, default: str
) -> str:
    """The expression wired to the input at ``index``, else ``default``."""
    terminal = terminal_at(node, "input", index)
    if terminal is None or not ctx.is_wired(terminal.id):
        return default
    return ctx.wired_input(terminal.id, node) or default


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The call on the front panel of the function the reference holds."""
    reference = reference_value(node, ctx)
    panel = f"{FRONT_PANEL_NAME}.of({reference})"
    result = terminal_at(node, "output", _RESULT)
    if node.method_code == CTRL_VAL_SET_CODE:
        call = (
            f"{panel}.set({_input_value(node, ctx, _NAME, repr(''))}, "
            f"{_input_value(node, ctx, _VALUE, 'None')})"
        )
    elif node.method_code == CTRL_VAL_GET_CODE:
        call = f"{panel}.get({_input_value(node, ctx, _NAME, repr(''))})"
    else:
        call = f"{panel}.get_all({_input_value(node, ctx, _CONTROLS, 'False')})"

    bindings: dict[str, str] = {}
    reference_out = terminal_at(node, "output", REFERENCE_OUT)
    if reference_out is not None:
        bindings[reference_out.id] = reference
    body: ast.stmt
    if result is not None and ctx.is_wired(result.id):
        variable = ctx.make_output_var(
            "controls" if node.method_code == CTRL_VAL_GET_ALL_CODE else "value",
            node.id,
            result.id,
        )
        body = build_assign(variable, parse_expr(call))
        bindings[result.id] = variable
    else:
        body = ast.Expr(value=parse_expr(call))
    ctx.import_front_panel()
    return report_panel_error(node, ctx, [body], bindings)


def report_panel_error(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    statements: list[ast.stmt],
    bindings: dict[str, str],
    after: list[ast.stmt] | None = None,
) -> CodeFragment:
    """``statements``, which may fail with the front panel's own error, run in a
    ``try``: that error is the node's own error (``CodeFragment.own_error``) and
    the outputs the statements assign take their defaults. ``after`` runs only
    when they did not fail."""
    own_error = ctx.make_output_var("own_error", node.id)
    exception = ctx.make_output_var("exception", node.id)
    defaults = output_defaults(node, bindings, assigned_names(statements), None)
    handler = ast.ExceptHandler(
        type=ast.Name(id=FRONT_PANEL_ERROR_NAME, ctx=ast.Load()),
        name=exception,
        body=[*defaults, build_assign(own_error, parse_expr(exception))],
    )
    attempt = ast.Try(
        body=cast("list[ast.stmt]", statements),
        handlers=[handler],
        orelse=[*(after or []), build_assign(own_error, parse_expr("None"))],
        finalbody=[],
    )
    return CodeFragment(statements=[attempt], bindings=bindings, own_error=own_error)

"""The front panel of a VI reached through a VI reference: the methods Ctrl Val.Set,
Ctrl Val.Get and Ctrl Val.Get All, and Run VI on a reference the code holds.

Every VI has a front panel that holds the value of each control and indicator
between calls, which a Python function does not. Each generated function carries
it as ``function.panel``, a ``Panel`` of the package's ``panels`` module
(``codegen/panels_module.py``), and ``panel_of(reference)`` is the panel of the
VI a reference holds:

- Ctrl Val.Set is ``panel_of(reference).set_variant(name, value)``: "Sets the
  value of a named control or indicator of the variant" (NI:
  properties-and-methods/vi-server/vi/setctrlvalvariant.html);
- Ctrl Val.Get is ``.get_variant(name)``: "Gets the value of a named control or
  indicator as variant data";
- Ctrl Val.Get All is ``.get_all_variants(controls)``: "an array of clusters that
  contains control and indicator names and their values as variant data", every
  control when its Controls input is TRUE and, FALSE by default, every indicator;
- Run VI is ``.run(wait)``: it "uses the current values of all front panel
  controls for execution rather than using data passed in through parameters".

The value and the result are a ``Variant`` (``variants_module``); the panel
holds a control's value in the control's own type, so a Set stores the data of the
variant (an empty variant gives error 1) and a Get gives the value with the type
the panel records for the control (``panels_module``, ``panel_types``).

A name the VI has no control or indicator of is error 1 with the message
``"<name>" not found`` (no NI page gives this error; an NI forum thread, "Help
with Set Control Value, Control Name", reports ERR1 and that message for a wrong
control name), and a reference that is Not a Refnum, or holds an object with no
panel, is error 1026, both reported on the Invoke Node's own error out
(``error_flow``). A name that two controls or indicators of the VI share is the
first object's in the front panel tabbing order, as NI says ("the first object
whose label matches").

The deprecated Ctrl Val.Set and Ctrl Val.Get All take and give each value as flattened
data and its type string (the connector pane of the node records an array of
clusters of ``name``, ``type desc`` ([I16]) and ``flat data`` (a string) for Get All,
and the inputs ``name``, ``type desc`` and ``flat data`` for Set; no NI page
documents them, the public table of contents lists the variant forms only). They
are the same two methods with the value flattened in LabVIEW's big-endian form:
Get All is ``.get_all_flattened(controls)`` (each control's ``Variant`` flattened
with its type string, ``variant_to_flattened``) and Set is ``.set_flattened(name,
type_string, data)`` (``variant_from_flattened`` then ``set_variant``), with the same
errors.
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
from ..panels_module import PANEL_CALL_IMPORTS, PANEL_ERROR_NAME, PANEL_OF_NAME
from ..variant_values import variant_input

CTRL_VAL_SET_CODE = 1051
CTRL_VAL_GET_CODE = 1052
CTRL_VAL_GET_ALL_CODE = 1053
# The method codes the corpus records for Ctrl Val.Set (Deprecated) and Ctrl
# Val.Get All (Deprecated).
CTRL_VAL_SET_DEPRECATED_CODE = 1009
CTRL_VAL_GET_ALL_DEPRECATED_CODE = 1020
_CODES = (
    CTRL_VAL_SET_CODE,
    CTRL_VAL_GET_CODE,
    CTRL_VAL_GET_ALL_CODE,
    CTRL_VAL_SET_DEPRECATED_CODE,
    CTRL_VAL_GET_ALL_DEPRECATED_CODE,
)
_GET_ALL_CODES = (CTRL_VAL_GET_ALL_CODE, CTRL_VAL_GET_ALL_DEPRECATED_CODE)

# The Invoke Node's terminals: the VI reference in and out, then the method's
# parameters from index 5 (a result at 5, its parameters at 6 and 8; the deprecated
# Set's flattened data at 10).
REFERENCE_IN = 0
REFERENCE_OUT = 1
_RESULT = 5
_NAME = 6
_VALUE = 8
_FLAT_DATA = 10
_CONTROLS = 6


def is_ctrl_val(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node calling Ctrl Val.Set, Get or Get All, or
    a deprecated Set or Get All, on a VI reference."""
    return node.object_name == VI_CLASS and node.method_code in _CODES


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


def input_value(
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
    panel = f"{PANEL_OF_NAME}({reference})"
    result = terminal_at(node, "output", _RESULT)
    if node.method_code == CTRL_VAL_SET_CODE:
        value = variant_input(ctx, node, terminal_at(node, "input", _VALUE))
        name = input_value(node, ctx, _NAME, repr(""))
        call = f"{panel}.set_variant({name}, {value})"
    elif node.method_code == CTRL_VAL_SET_DEPRECATED_CODE:
        name = input_value(node, ctx, _NAME, repr(""))
        type_string = input_value(node, ctx, _VALUE, "[]")
        data = input_value(node, ctx, _FLAT_DATA, repr(""))
        call = f"{panel}.set_flattened({name}, {type_string}, {data})"
    elif node.method_code == CTRL_VAL_GET_CODE:
        call = f"{panel}.get_variant({input_value(node, ctx, _NAME, repr(''))})"
    elif node.method_code == CTRL_VAL_GET_ALL_DEPRECATED_CODE:
        controls = input_value(node, ctx, _CONTROLS, "False")
        call = f"{panel}.get_all_flattened({controls})"
    else:
        controls = input_value(node, ctx, _CONTROLS, "False")
        call = f"{panel}.get_all_variants({controls})"

    bindings: dict[str, str] = {}
    reference_out = terminal_at(node, "output", REFERENCE_OUT)
    if reference_out is not None:
        bindings[reference_out.id] = reference
    body: ast.stmt
    if result is not None and ctx.is_wired(result.id):
        variable = ctx.make_output_var(
            "controls" if node.method_code in _GET_ALL_CODES else "value",
            node.id,
            result.id,
        )
        body = build_assign(variable, parse_expr(call))
        bindings[result.id] = variable
    else:
        body = ast.Expr(value=parse_expr(call))
    ctx.import_panels(PANEL_CALL_IMPORTS)
    return report_panel_error(node, ctx, [body], bindings)


def report_panel_error(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    statements: list[ast.stmt],
    bindings: dict[str, str],
    after: list[ast.stmt] | None = None,
    catches: str = PANEL_ERROR_NAME,
) -> CodeFragment:
    """``statements``, which may fail with the front panel's own error (or the
    class ``catches`` names), run in a ``try``: that error is the node's own error
    (``CodeFragment.own_error``) and the outputs the statements assign take their
    defaults. ``after`` runs only when they did not fail."""
    own_error = ctx.make_output_var("own_error", node.id)
    exception = ctx.make_output_var("exception", node.id)
    defaults = output_defaults(node, ctx, bindings, assigned_names(statements), None)
    handler = ast.ExceptHandler(
        type=ast.Name(id=catches, ctx=ast.Load()),
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

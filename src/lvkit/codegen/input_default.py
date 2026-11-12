"""An unwired input holds its control's default inside the function.

An input a caller leaves unwired takes the control's saved default, else its
type's default; it is never "no value". A ``def`` default is a literal for a
number, string, Boolean, enum or ring and ``None`` for any other type with a
default of its own (an array, a cluster, a class), because a ``def``
default is evaluated once and an object there would be shared by every call.
The function's first statements make the default for such a parameter::

    def f(*, test_in: TestCase | None = None, rows: list[int] | None = None):
        if test_in is None:
            test_in = TestCase()
        if rows is None:
            rows = []

An error in is none: no error is its default, so it gets no statement. A class
that is not loaded has no Python class to make the object from: that branch is
a gap (it raises where it would run) and a call that wires the input still works.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.graph.interface_order import is_required
from lvkit.lv_type_codes import is_variant_type_name
from lvkit.models import Terminal

from .ast_utils import (
    parameter_default_expr,
    saved_default_expr,
    type_default_expr,
)
from .type_default import default_if_none, is_class_type, type_default

if TYPE_CHECKING:
    from .context import CodeGenContext


def is_none_defaulted(term: Terminal) -> bool:
    """Whether ``term`` is an input a caller may leave unwired whose control
    default is a value its ``def`` cannot hold (an array, a cluster, a class
    object): the parameter is typed ``| None`` and the function makes the default
    when it is ``None``. A Required input is always wired and has none. A path has
    none: Not a Path is ``None`` in Python, a value a caller can pass, so ``None``
    there is never "left unwired"."""
    lv_type = term.lv_type
    if lv_type is None or term.is_error_cluster or is_required(term, "input"):
        return False
    if is_class_type(lv_type) or is_variant_type_name(lv_type.underlying_type):
        return True
    if lv_type.underlying_type == "Path":
        return False
    return _is_none(parameter_default_expr(term)) and not _is_none(
        type_default_expr(lv_type)
    )


def _is_none(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Constant) and expression.value is None


def build_input_defaults(
    inputs: list[Terminal], names: dict[str, str], ctx: CodeGenContext
) -> list[ast.stmt]:
    """``if name is None: name = <the control's default>`` for each input a
    caller may leave unwired and whose ``def`` default is ``None``
    (``names``: terminal id to parameter name). When the default is a class that
    is not loaded the branch raises the gap instead."""
    stmts: list[ast.stmt] = []
    for inp in inputs:
        if not is_none_defaulted(inp) or inp.id not in names:
            continue
        name = names[inp.id]
        label = inp.name or name
        stmts.append(
            default_if_none(
                name,
                lambda inp=inp, label=label: _control_default(inp, label, ctx),
                ctx,
                f"input {label}",
            )
        )
    return stmts


def _control_default(inp: Terminal, label: str, ctx: CodeGenContext) -> ast.expr:
    saved = saved_default_expr(inp.default_value, inp.lv_type)
    if saved is not None:
        return saved
    return type_default(
        ctx, inp.lv_type, f"The default of the unwired input '{label}' of"
    )

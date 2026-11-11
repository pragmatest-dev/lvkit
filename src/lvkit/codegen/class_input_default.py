"""An unwired class input is the class's default object.

A connector-pane input of a LabVIEW class always holds an object of that class;
left unwired it holds the class's default object (a class has no saved state
beyond its default). The function's parameter is ``None`` when not wired (a
``def`` default is evaluated once, so an object there would be shared by every
call) and the function's first statements make a new default object of the class
for it::

    def f(*, test_in: TestCase | None = None):
        if test_in is None:
            test_in = TestCase()

A class that is not loaded has no Python class to make the object from: that
branch is a gap (it raises where it would run) and a call that wires the input
still works.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.graph.interface_order import is_required
from lvkit.models import Terminal

from .ast_utils import parse_expr
from .type_default import default_if_none

if TYPE_CHECKING:
    from .context import CodeGenContext


def has_class_default(term: Terminal) -> bool:
    """Whether ``term`` is a class input a caller may leave unwired (a Required
    one is always wired)."""
    return (
        term.lv_type is not None
        and term.lv_type.ref_type == "UDClassInst"
        and not is_required(term, "input")
    )


def build_class_input_defaults(
    inputs: list[Terminal], names: dict[str, str], ctx: CodeGenContext
) -> list[ast.stmt]:
    """``if name is None: name = Class()`` for each class input a caller may
    leave unwired (``names``: terminal id to parameter name). When the class is
    not loaded the branch raises the gap instead."""
    stmts: list[ast.stmt] = []
    for inp in inputs:
        if not has_class_default(inp) or inp.id not in names or inp.lv_type is None:
            continue
        name = names[inp.id]
        label = inp.name or name
        lv_type = inp.lv_type
        stmts.append(
            default_if_none(
                name,
                lambda lv_type=lv_type, label=label: parse_expr(
                    f"{ctx.class_expression(lv_type, _reason_start(label))}()"
                ),
                ctx,
                f"input {label}",
            )
        )
    return stmts


def _reason_start(label: str) -> str:
    return f"The default object of the unwired input '{label}' of"

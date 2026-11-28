"""Shared pieces of the rounding and To <integer> conversion ops."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.models import Terminal

from ...ast_utils import parse_expr
from ...elementwise import arrayify


def saturating_round(value: str, bits: int, signed: bool) -> str:
    """The integer of ``bits`` bits that the floating-point ``value`` (a name or
    expression) converts to: rounded to the nearest, ties to even, and past the
    range the minimum or maximum, so infinity is an end of the range (the range is
    tested before ``round``, which raises for infinity)."""
    low = -(2 ** (bits - 1)) if signed else 0
    high = 2 ** (bits - 1) - 1 if signed else 2**bits - 1
    return (
        f"({low} if {value} < {low} else "
        f"{high} if {value} > {high} else round({value}))"
    )


def number_input(node: PrimitiveNode) -> Terminal | None:
    """The ``number`` / ``x`` input (terminal index 1) of a one-input numeric
    function."""
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1), None
    )


def over_array_rank(expression: str, number: Terminal | None) -> str:
    """``expression`` of ``in_1``, applied to every element when ``number`` is an
    array (one comprehension level per dimension)."""
    rank = array_rank(number.lv_type) if number is not None else 0
    if not rank:
        return expression
    lifted, _ = arrayify(parse_expr(expression), {"in_1": rank}, conditionals=True)
    return ast.unparse(lifted)

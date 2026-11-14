"""Decimate 1D Array (the ``decimate`` node).

NI, "Decimate 1D Array Function" (labview-api-ref,
``functions/decimate-1d-array.html``): "Divides the elements of array into the
output arrays, placing elements into the outputs successively. This function drops
any elements that cause the output arrays to have different lengths." With n output
terminals (resizing the function sets n) output k holds elements k, k+n, k+2n, ...
of the array, and every output is as long as the shortest, ``len(array) // n``: 16
elements into 4 outputs give 0, 4, 8, 12 / 1, 5, 9, 13 / ...; 15 elements give
three elements in each, the last output losing its element 15.

Terminals (a generic node class): in 0 = array; out 1 .. n = the decimated arrays,
wired or not (n counts them all).
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    outputs = sorted(
        (t for t in node.terminals if t.direction == "output"),
        key=lambda t: t.index,
    )
    array_term = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0), None
    )
    if array_term is None or not outputs:
        raise ConversionGap("Decimate 1D Array has no array input or no output", node)
    array = ctx.wired_input(array_term.id, node)
    if array is None:
        raise ConversionGap("Decimate 1D Array has no array wired", node)
    statements: list[ast.stmt] = []
    if not array.isidentifier():
        name = ctx.make_output_var("array", node.id)
        statements.append(build_assign(name, parse_expr(array)))
        array = name
    count = len(outputs)
    bindings: dict[str, str] = {}
    for position, terminal in enumerate(outputs):
        if not ctx.is_wired(terminal.id):
            continue
        name = ctx.make_output_var("decimated_array", node.id, terminal_id=terminal.id)
        end = f"len({array}) // {count} * {count}"
        statements.append(
            build_assign(name, parse_expr(f"{array}[{position}:{end}:{count}]"))
        )
        bindings[terminal.id] = name
        ctx.array_vars[name] = 1
    return CodeFragment(statements=statements, bindings=bindings)

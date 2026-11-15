"""Interleave 1D Arrays (the ``interLeave`` node).

NI, "Interleave 1D Arrays Function" (labview-api-ref,
``functions/interleave-1d-arrays.html``): "Interleaves corresponding elements from
the input arrays into a single output array." ``interleaved array [0]`` is
``array 0 [0]``, ``[1]`` is ``array 1 [0]``, ... ``[n]`` is ``array 0 [1]`` (n
being the number of input terminals), and "if the input array is not the same
size, the number of elements in interleaved array equals the number of elements in
the smallest input array multiplied by the number of input arrays": arrays
[0, 3], [1, 4], [2, 5] give [0, 1, 2, 3, 4, 5], and [0, 3, 5], [1], [2, 4] give
[0, 1, 2]. ``zip`` stops at the shortest array, which is that rule.

Terminals (a generic node class): out 0 = the interleaved array; in 1 .. n = the
arrays. NI does not say what an input with nothing wired contributes, so a node
with one is a gap.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    inputs = sorted(
        (t for t in node.terminals if t.direction == "input"), key=lambda t: t.index
    )
    output = next((t for t in node.terminals if t.direction == "output"), None)
    if output is None or not inputs:
        raise ConversionGap("Interleave 1D Arrays has no array input or output", node)
    wired = [ctx.wired_input(t.id, node) for t in inputs]
    arrays = [value for value in wired if value]
    if len(arrays) != len(wired):
        raise ConversionGap(
            "Interleave 1D Arrays has an array input with nothing wired; NI does "
            "not document what that input contributes",
            node,
        )
    if not ctx.is_wired(output.id):
        return CodeFragment.empty()
    name = ctx.make_output_var("interleaved_array", node.id, terminal_id=output.id)
    statements: list[ast.stmt] = [
        build_assign(
            name,
            parse_expr(f"[x for row in zip({', '.join(arrays)}) for x in row]"),
        )
    ]
    ctx.array_vars[name] = 1
    return CodeFragment(statements=statements, bindings={output.id: name})

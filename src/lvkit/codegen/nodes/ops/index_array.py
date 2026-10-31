"""Index Array (node class ``aIndx``).

LabVIEW's Index Array returns the array element's DEFAULT value for an
out-of-range index — it never errors — whereas a plain Python subscript raises
``IndexError``. So a scalar-element (1D) index is lowered to a guarded
expression carrying the element type's real default; a byte value indexing a
short lookup table (e.g. OpenG Trim Whitespace's 33-entry table) then yields the
default instead of crashing. The guard is written inline: ``a[i] if 0 <= i <
len(a) else default``.

Source-declared one-dimensional arrays use separate output/index rows. Other
ranks use the expression template, with an empty-array default for subarrays.

Terminals (from the node-type entry, laid out by ``terminal_roles``):
  in 0 = array, then per set: an element output and its index input
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.index_array_plan import IndexUse, plan_index_array
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.unwired_default import unwired_behavior
from lvkit.models import LVTypeKind, Terminal
from lvkit.primitive_resolver import ResolvedPrimitive
from lvkit.terminal_default import DefaultBehavior

from ...ast_utils import build_assign, default_value_expr, parse_expr
from ...context import CodeGenContext
from ...errors import CodeGenError
from ...fragment import CodeFragment
from ...native_array import dimension_sizes, guarded_index
from ..terminal_roles import laid_out_roles
from . import register_op, register_op_fragment


@register_op_fragment("INDEX_ARRAY")
def generate_1d_index_array(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    """Emit saved output/index pairs for a source-declared 1D array.

    Each wired index starts that row; an unwired first index is zero and each
    subsequent unwired index is the preceding index plus one. Unwired outputs
    still advance the row index. Other ranks use the expression-template path.
    """
    if node.node_type != "aIndx" or not node.terminals:
        return None
    array = next((t for t in node.terminals if t.index == 0), None)
    if (
        array is None
        or array.direction != "input"
        or array.lv_type is None
        or array.lv_type.kind != LVTypeKind.ARRAY
    ):
        return None
    if (array.lv_type.dimensions or 1) > 1:
        return _generate_nd_index_array(node, ctx, array)
    # Roles come from the entry's layout for this node's array type: after the
    # array, one set per element output, each with its index input.
    try:
        roles = laid_out_roles(node, "aIndx")
    except ConversionGap as error:
        raise ConversionGap(
            "1D Index Array requires output/index terminal pairs", node
        ) from error
    outputs = [term for _, term in roles.get(1, [])]
    indices = [term for _, term in roles.get(2, [])]
    if (
        len(outputs) != len(indices)
        or any(t.direction != "output" for t in outputs)
        or any(t.direction != "input" for t in indices)
    ):
        raise ConversionGap("1D Index Array requires output/index terminal pairs", node)
    if any(t.lv_type is None or t.lv_type.kind == LVTypeKind.ARRAY for t in outputs):
        raise ConversionGap("1D Index Array requires typed scalar outputs", node)

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    array_expr = ctx.wired_input(array.id, node)
    # Evaluate the source expression once for every row in the expanded node.
    array_var = ctx.make_output_var("indexed_array", node.id)
    statements.append(
        build_assign(
            array_var, parse_expr(array_expr if array_expr is not None else "[]")
        )
    )
    previous_index: str | None = None
    for position, (output, index) in enumerate(zip(outputs, indices)):
        expr = ctx.wired_input(index.id, node)
        if expr is None:
            follows_previous = (
                unwired_behavior(node, index) == DefaultBehavior.NEXT_INDEX
            )
            expr = f"{previous_index} + 1" if follows_previous else "0"
        index_var = ctx.make_output_var(f"array_index_{position}", node.id)
        statements.append(build_assign(index_var, parse_expr(expr)))
        previous_index = index_var
        if not ctx.is_wired(output.id):
            continue
        variable = ctx.make_output_var(
            f"element_{position}", node.id, terminal_id=output.id
        )
        statements.append(
            build_assign(
                variable,
                parse_expr(
                    guarded_index(
                        array_var,
                        index_var,
                        ast.unparse(default_value_expr(output.lv_type)),
                    )
                ),
            )
        )
        bindings[output.id] = variable
    return CodeFragment(statements=statements, bindings=bindings)


def _generate_nd_index_array(
    node: PrimitiveNode, ctx: CodeGenContext, array: Terminal
) -> CodeFragment:
    """Index Array on an array of several dimensions, one set of terminals.

    The plan (``graph.index_array_plan``) says how each index is used: a wired
    index picks its position, an unwired one that the output's rank says is indexed
    picks 0, and the rest of the dimensions are taken whole. An index outside its
    dimension gives an empty array when what is selected is still an array, else
    the element default. Several output sets are not converted.
    """
    assert array.lv_type is not None and array.lv_type.element_type is not None
    plan = plan_index_array(
        node.terminals, lambda t: ctx.is_wired(t.id) or ctx.resolve(t.id) is not None
    )
    if plan is None:
        raise ConversionGap(
            "Index Array of a multi-dimensional array whose output type does not "
            "match its indexes is not converted",
            node,
        )
    rank = plan.rank
    if not ctx.is_wired(plan.output.id):
        return CodeFragment.empty()
    array_expr = ctx.wired_input(array.id, node)
    source = array_expr if array_expr is not None else "[]"
    picks: list[str | None] = []
    for index, use in zip(plan.indices, plan.uses):
        if use == IndexUse.WIRED:
            text = ctx.wired_input(index.id, node)
            if text is None:
                raise CodeGenError("Unresolved wired Index Array index", node)
            picks.append(text)
        else:
            picks.append("0" if use == IndexUse.ZERO else None)
    picks.extend([None] * (rank - len(picks)))
    sizes = dimension_sizes(source, rank)
    guards = [
        f"0 <= {pick} < {sizes[depth]}"
        for depth, pick in enumerate(picks)
        if pick is not None
    ]
    still_array = (plan.output.lv_type is not None) and (
        plan.output.lv_type.kind == LVTypeKind.ARRAY
    )
    default = (
        "[]"
        if still_array
        else ast.unparse(default_value_expr(array.lv_type.element_type))
    )

    def select(held: str, depth: int) -> str:
        if depth == rank or all(p is None for p in picks[depth:]):
            return held
        pick = picks[depth]
        if pick is None:
            row = f"_row{depth}"
            return f"[{select(row, depth + 1)} for {row} in {held}]"
        return select(f"{held}[{pick}]", depth + 1)

    expression = select(source, 0)
    if guards:
        expression = f"({expression} if {' and '.join(guards)} else {default})"
    variable = ctx.make_output_var("element", node.id, terminal_id=plan.output.id)
    return CodeFragment(
        statements=[build_assign(variable, parse_expr(expression))],
        bindings={plan.output.id: variable},
    )


@register_op("INDEX_ARRAY")
def index_array(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    out = next((t for t in node.terminals if t.direction == "output"), None)
    elem = out.lv_type if out else None
    if elem is not None and elem.kind == LVTypeKind.ARRAY:
        # Partial index of a multi-dimensional array: indexing one dimension
        # yields a SUBARRAY (e.g. a row of a 2-D array). LabVIEW returns an EMPTY
        # subarray for an out-of-range index rather than raising, so use the
        # guarded index with an empty-array default -- a real caller with a
        # populated array is unaffected, and an empty/short array no longer
        # IndexErrors (e.g. Sort/Reverse 2D Array on an empty input).
        return guarded_index("in_0", "in_2", "[]")
    # 1D: the element's real default on an out-of-range index.
    return guarded_index("in_0", "in_2", ast.unparse(default_value_expr(elem)))

"""Index Array (node class ``aIndx``).

LabVIEW's Index Array returns the array element's DEFAULT value for an
out-of-range index — it never errors — whereas a plain Python subscript raises
``IndexError``. So a scalar-element (1D) index is lowered to a guarded
expression carrying the element type's real default; a byte value indexing a
short lookup table (e.g. OpenG Trim Whitespace's 33-entry table) then yields the
default instead of crashing.

Source-declared one-dimensional arrays use separate output/index rows. Other
ranks use the expression template, with an empty-array default for subarrays.

Terminals (from the node-type entry, laid out by ``terminal_roles``):
  in 0 = array, then per set: an element output and its index input
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import build_assign, default_value_expr, parse_expr
from ...context import CodeGenContext
from ...elementwise import LV_IMPORT
from ...fragment import CodeFragment
from ..base import CodeGenError
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
        or array.lv_type.dimensions != 1
    ):
        return None
    # Roles come from the entry's layout for this node's array type: after the
    # array, one set per element output, each with its index input.
    try:
        roles = laid_out_roles(node, "aIndx")
    except CodeGenError as error:
        raise CodeGenError(
            "1D Index Array requires output/index terminal pairs", node
        ) from error
    outputs = [term for _, term in roles.get(1, [])]
    indices = [term for _, term in roles.get(2, [])]
    if (
        len(outputs) != len(indices)
        or any(t.direction != "output" for t in outputs)
        or any(t.direction != "input" for t in indices)
    ):
        raise CodeGenError("1D Index Array requires output/index terminal pairs", node)
    if any(t.lv_type is None or t.lv_type.kind == LVTypeKind.ARRAY for t in outputs):
        raise CodeGenError("1D Index Array requires typed scalar outputs", node)

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    array_expr = ctx.resolve(array.id)
    if array_expr is None and ctx.is_wired(array.id):
        raise CodeGenError("Unresolved wired 1D Index Array input", node)
    # Evaluate the source expression once for every row in the expanded node.
    array_var = ctx.make_output_var("indexed_array", node.id)
    statements.append(
        build_assign(
            array_var, parse_expr(array_expr if array_expr is not None else "[]")
        )
    )
    previous_index: str | None = None
    for position, (output, index) in enumerate(zip(outputs, indices)):
        expr = ctx.resolve(index.id)
        if expr is None:
            if ctx.is_wired(index.id):
                raise CodeGenError("Unresolved wired 1D Index Array index", node)
            expr = "0" if previous_index is None else f"{previous_index} + 1"
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
                ast.Call(
                    func=ast.Attribute(
                        value=ast.Name(id="_lv", ctx=ast.Load()),
                        attr="index_array",
                        ctx=ast.Load(),
                    ),
                    args=[
                        ast.Name(id=array_var, ctx=ast.Load()),
                        ast.Name(id=index_var, ctx=ast.Load()),
                        default_value_expr(output.lv_type),
                    ],
                    keywords=[],
                ),
            )
        )
        bindings[output.id] = variable
    return CodeFragment(statements=statements, bindings=bindings, imports={LV_IMPORT})


@register_op("INDEX_ARRAY")
def index_array(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    out = next((t for t in node.terminals if t.direction == "output"), None)
    elem = out.lv_type if out else None
    if elem is not None and elem.kind == LVTypeKind.ARRAY:
        # Partial index of a multi-dimensional array: indexing one dimension
        # yields a SUBARRAY (e.g. a row of a 2-D array). LabVIEW returns an EMPTY
        # subarray for an out-of-range index rather than raising, so use the
        # runtime helper with an empty-array default -- a real caller with a
        # populated array is unaffected, and an empty/short array no longer
        # IndexErrors (e.g. Sort/Reverse 2D Array on an empty input).
        return "_lv.index_array(in_0, in_2, [])"
    # 1D: return the element's real default on out-of-range (LabVIEW semantics)
    # via the runtime helper, so the array expression is evaluated once.
    default = ast.unparse(default_value_expr(elem))
    return f"_lv.index_array(in_0, in_2, {default})"

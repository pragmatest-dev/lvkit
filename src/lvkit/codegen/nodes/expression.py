"""Code generation for Expression Nodes (exprNode).

The formula is emitted as one Python expression at the node's place
(``lvkit.formula.emit.emit_expression``). An array input is processed element by
element, one comprehension level per dimension, as the Expression Node applies
its expression to each element of an array.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.formula.emit import emit_expression
from lvkit.formula.expression import expression_variable
from lvkit.graph.models import FormulaNode
from lvkit.models import LVType, LVTypeKind

from ..ast_utils import to_var_name
from ..fragment import CodeFragment

if TYPE_CHECKING:
    from ..context import CodeGenContext


def _numeric_element(node: FormulaNode, lv_type: LVType) -> tuple[str, int]:
    """The numeric type name of the elements of ``lv_type`` and its dimension
    count (0 for a scalar)."""
    dimensions = 0
    while lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        dimensions += lv_type.dimensions or 1
        lv_type = lv_type.element_type
    if lv_type.kind != LVTypeKind.PRIMITIVE or not lv_type.underlying_type:
        raise ConversionGap(
            f"Expression Node {node.id}: the input is a {lv_type.kind.value}; "
            "only a number or an array of numbers is converted"
        )
    return lv_type.underlying_type, dimensions


def _is_plain_read(expr: ast.expr) -> bool:
    """Whether ``expr`` is a name, or an attribute or constant-index read of one."""
    if isinstance(expr, ast.Name):
        return True
    if isinstance(expr, ast.Attribute):
        return _is_plain_read(expr.value)
    if isinstance(expr, ast.Subscript):
        return isinstance(expr.slice, ast.Constant) and _is_plain_read(expr.value)
    return False


def generate(node: FormulaNode, ctx: CodeGenContext) -> CodeFragment:
    if not node.script:
        raise ConversionGap(
            f"Expression Node {node.id} has no formula text: the VI does not "
            "store it where lvkit reads it"
        )
    inputs = [t for t in node.terminals if t.direction == "input"]
    outputs = [t for t in node.terminals if t.direction == "output"]
    if len(inputs) != 1 or len(outputs) != 1:
        raise ConversionGap(f"Expression Node {node.id} needs one input and one output")
    source, result = inputs[0], outputs[0]
    if source.lv_type is None:
        raise ConversionGap(f"Expression Node {node.id}: its input has no type")
    numeric, dimensions = _numeric_element(node, source.lv_type)

    variable = expression_variable(node.script)
    wired = ctx.wired_input(source.id, node)
    if wired is None:
        wired = "[]" if dimensions else "0"
    statements: list[ast.stmt] = []
    if dimensions:
        # The input is the iterable of the outermost comprehension, evaluated
        # once. Each inner level iterates the element of the level above.
        loop = to_var_name(variable)
        value = emit_expression(node.script, variable, numeric, loop)
        levels = [f"{loop}_{level}" for level in range(dimensions - 1)] + [loop]
        text = value.source
        for level in reversed(range(dimensions)):
            iterable = wired if level == 0 else levels[level - 1]
            text = f"[{text} for {levels[level]} in {iterable}]"
    else:
        # A name, attribute path or constant-index read (``a.b[1].c``) binds
        # tighter than any operator and reads the same each time, so it is
        # written in place. Anything else is bound once and the expression
        # reads the name.
        if not _is_plain_read(ast.parse(wired, mode="eval").body):
            bound = ctx.make_output_var("expr_input", node.id)
            statements += ast.parse(f"{bound} = {wired}").body
            wired = bound
        value = emit_expression(node.script, variable, numeric, wired)
        text = value.source

    out_var = ctx.make_output_var("expr", node.id, terminal_id=result.id)
    statements += ast.parse(f"{out_var} = {text}").body
    return CodeFragment(
        statements=statements,
        bindings={result.id: out_var},
        imports=set(value.imports),
    )

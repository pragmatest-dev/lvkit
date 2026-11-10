"""Code generation for Expression Nodes (exprNode).

The formula is transpiled to a module-level Python function of its one variable
(``lvkit.formula.expression``), and the node is a call to it. An array input is
processed element by element, one comprehension level per dimension, as the
Expression Node applies its expression to each element of an array.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.formula.expression import (
    RESULT_NAME,
    expression_variable,
    transpile_expression,
)
from lvkit.graph.models import FormulaNode
from lvkit.models import LVType, LVTypeKind

from ..ast_utils import node_owned_name
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
    function = node_owned_name("_expression", ctx.function_name, node.id)
    transpiled = transpile_expression(node.script, variable, numeric, function)
    function = ctx.add_module_function(transpiled.func_def, owner=node.id)

    wired = ctx.wired_input(source.id, node)
    if wired is None:
        wired = "[]" if dimensions else "0"
    # One comprehension level per dimension, the innermost over the elements.
    element = f"_e{dimensions - 1}" if dimensions else wired
    value = f"{function}({variable}={element})[{RESULT_NAME!r}]"
    for level in reversed(range(dimensions)):
        value = (
            f"[{value} for _e{level} in {wired if level == 0 else f'_e{level - 1}'}]"
        )

    suffix = re.sub(r"\W", "_", node.id.split("::")[-1])
    out_var = ctx.make_output_var(f"expr_{suffix}", node.id, terminal_id=result.id)
    statements = ast.parse(f"{out_var} = {value}").body
    return CodeFragment(
        statements=statements,
        bindings={result.id: out_var},
        imports=set(transpiled.imports),
    )

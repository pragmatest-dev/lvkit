"""Code generator for compound operations (cpdArith, aBuild)."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import build_assign, parse_expr
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class CompoundArithCodeGen(NodeCodeGen):
    """Generate code for LabVIEW compound arithmetic operations.

    cpdArith combines multiple inputs with a single operation (typically OR for booleans).
    Used for combining error conditions or stop conditions.

    Structure:
    - Multiple input terminals (indices 1+)
    - One output terminal (index 0)
    - All inputs combined with OR to produce output
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for compound arithmetic (OR of inputs)."""
        terminals = node.terminals
        node_id = node.id

        # Separate inputs and output
        inputs = [t for t in terminals if t.direction == "input"]
        outputs = [t for t in terminals if t.direction == "output"]

        if not outputs:
            return CodeFragment()

        output_term = outputs[0]
        output_id = output_term.id

        # Resolve input values
        input_exprs = []
        for inp in sorted(inputs, key=lambda t: t.index):
            inp_id = inp.id
            val = ctx.resolve(inp_id)
            if val:
                input_exprs.append(val)

        if not input_exprs:
            # No inputs resolved - bind output to False
            var_name = f"cpd_{node_id}"
            stmt = build_assign(var_name, ast.Constant(value=False))
            return CodeFragment(
                statements=[stmt],
                bindings={output_id: var_name},
            )

        if len(input_exprs) == 1:
            # Single input - just pass through
            return CodeFragment(bindings={output_id: input_exprs[0]})

        # Multiple inputs - combine with 'or'
        # Build: input1 or input2 or input3 ...
        var_name = f"cpd_{node_id}"
        combined = parse_expr(input_exprs[0])
        for expr_str in input_exprs[1:]:
            combined = ast.BoolOp(
                op=ast.Or(),
                values=[combined, parse_expr(expr_str)],
            )

        stmt = build_assign(var_name, combined)
        return CodeFragment(
            statements=[stmt],
            bindings={output_id: var_name},
        )


class ArrayBuildCodeGen(NodeCodeGen):
    """Generate code for LabVIEW array build operations.

    aBuild collects multiple inputs into an array.

    Structure:
    - Multiple input terminals
    - One output terminal (the array)
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for array building."""
        terminals = node.terminals
        node_id = node.id

        # Separate inputs and output
        inputs = [t for t in terminals if t.direction == "input"]
        outputs = [t for t in terminals if t.direction == "output"]

        if not outputs:
            return CodeFragment()

        output_term = outputs[0]
        output_id = output_term.id

        # Resolve input values
        elements = []
        for inp in sorted(inputs, key=lambda t: t.index):
            inp_id = inp.id
            val = ctx.resolve(inp_id)
            if val:
                elements.append(parse_expr(val))
            else:
                # Missing input - use None as placeholder
                elements.append(ast.Constant(value=None))

        # Build list literal: [elem1, elem2, ...]
        var_name = f"arr_{node_id}"
        list_expr = ast.List(elts=elements, ctx=ast.Load())
        stmt = build_assign(var_name, list_expr)

        return CodeFragment(
            statements=[stmt],
            bindings={output_id: var_name},
        )

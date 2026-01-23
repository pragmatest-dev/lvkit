"""Code generator for compound operations (cpdArith, aBuild)."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
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
        """Generate code for compound arithmetic."""
        terminals = node.terminals
        operation = node.operation or "or"  # Default to OR for backwards compat

        # Separate inputs and output
        inputs = [t for t in terminals if t.direction == "input"]
        outputs = [t for t in terminals if t.direction == "output"]

        if not outputs:
            return CodeFragment()

        output_term = outputs[0]
        output_id = output_term.id

        # Resolve input values and collect names for semantic naming
        input_exprs = []
        input_names = []
        for inp in sorted(inputs, key=lambda t: t.index):
            inp_id = inp.id
            val = ctx.resolve(inp_id)
            if val:
                input_exprs.append(val)
                input_names.append(val)

        # Generate semantic variable name
        var_name = self._make_var_name(operation, input_names, ctx)

        if not input_exprs:
            # No inputs resolved - bind output to default value
            default_value = False if operation in ("or", "and") else 0
            stmt = build_assign(var_name, ast.Constant(value=default_value))
            return CodeFragment(
                statements=[stmt],
                bindings={output_id: var_name},
            )

        if len(input_exprs) == 1:
            # Single input - just pass through
            return CodeFragment(bindings={output_id: input_exprs[0]})

        # Multiple inputs - combine with appropriate operator
        combined = parse_expr(input_exprs[0])

        if operation == "or":
            for expr_str in input_exprs[1:]:
                combined = ast.BoolOp(
                    op=ast.Or(),
                    values=[combined, parse_expr(expr_str)],
                )
        elif operation == "and":
            for expr_str in input_exprs[1:]:
                combined = ast.BoolOp(
                    op=ast.And(),
                    values=[combined, parse_expr(expr_str)],
                )
        elif operation == "add":
            for expr_str in input_exprs[1:]:
                combined = ast.BinOp(
                    left=combined,
                    op=ast.Add(),
                    right=parse_expr(expr_str),
                )
        else:
            # Unknown operation - fall back to OR
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

    def _make_var_name(
        self, operation: str, input_names: list[str], ctx: CodeGenContext
    ) -> str:
        """Generate a semantic variable name for compound arithmetic.

        For boolean operations (or/and), uses "should_stop" for stop conditions.
        Falls back to combining input variable names or a generic name.
        """
        # For boolean operations, use semantic name
        if operation in ("or", "and"):
            # Check if any input suggests this is a stop/done condition
            stop_keywords = {"stop", "done", "exit", "quit", "end", "finish", "complete"}
            for name in input_names:
                name_lower = name.lower()
                if any(kw in name_lower for kw in stop_keywords):
                    return "should_stop"

            # Default semantic name for boolean combinations
            return "should_stop"

        # For arithmetic operations, try to derive from inputs
        if operation == "add" and input_names:
            # Use "total" or "sum" for addition
            return "total"

        # Generic fallback
        return "combined"


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

        # Separate inputs and output
        inputs = [t for t in terminals if t.direction == "input"]
        outputs = [t for t in terminals if t.direction == "output"]

        if not outputs:
            return CodeFragment()

        output_term = outputs[0]
        output_id = output_term.id

        # Resolve input values and collect names for semantic naming
        elements = []
        input_names = []
        for inp in sorted(inputs, key=lambda t: t.index):
            inp_id = inp.id
            val = ctx.resolve(inp_id)
            if val:
                elements.append(parse_expr(val))
                input_names.append(val)
            else:
                # Missing input - use None as placeholder
                elements.append(ast.Constant(value=None))

        # Generate semantic variable name
        var_name = self._make_var_name(input_names, ctx)
        list_expr = ast.List(elts=elements, ctx=ast.Load())
        stmt = build_assign(var_name, list_expr)

        return CodeFragment(
            statements=[stmt],
            bindings={output_id: var_name},
        )

    def _make_var_name(self, input_names: list[str], ctx: CodeGenContext) -> str:
        """Generate a semantic variable name for array building.

        Tries to derive a meaningful name from the input variable names.
        """
        if not input_names:
            return "items"

        # Try to find a common base name from inputs
        # e.g., ["path_part_1", "path_part_2"] -> "path_parts"
        # e.g., ["name", "value"] -> "items" (no common base)

        # Extract base words from first input (before any trailing numbers/indices)
        first = input_names[0]
        base = to_var_name(first).rstrip("0123456789_")

        if base and len(base) > 2:
            # Check if base is common to multiple inputs
            common = all(base in to_var_name(n) for n in input_names[:3])
            if common:
                # Pluralize the base name
                if base.endswith("y") and not base.endswith(("ay", "ey", "oy", "uy")):
                    return base[:-1] + "ies"
                elif base.endswith(("s", "x", "z", "ch", "sh")):
                    return base + "es"
                else:
                    return base + "s"

        # Fallback: use "items" or derive from context
        return "items"

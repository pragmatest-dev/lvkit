"""Code generator for constants."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from ..ast_utils import to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class ConstantCodeGen(NodeCodeGen):
    """Generate code for constant values.

    Constants are typically pre-bound in context with their literal value.
    This generator handles any that need explicit assignment.
    """

    def generate(self, node: dict[str, Any], ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a constant node.

        Usually constants are already bound in context, so this may produce
        no statements. For labeled constants, it produces an assignment.
        """
        const_id = node.get("id")
        if not const_id:
            return CodeFragment.empty()

        # Check if already bound
        if const_id in ctx.bindings:
            return CodeFragment.empty()

        # Get value and label
        value = node.get("value")
        label = node.get("label") or node.get("name")

        if label:
            # Named constant - emit assignment
            var_name = to_var_name(label)
            value_ast = self._parse_value(value)

            stmt = ast.Assign(
                targets=[ast.Name(id=var_name, ctx=ast.Store())],
                value=value_ast,
            )

            return CodeFragment(
                statements=[stmt],
                bindings={const_id: var_name},
            )
        else:
            # Unnamed constant - just bind the value directly
            value_repr = self._value_to_repr(value)
            return CodeFragment(
                statements=[],
                bindings={const_id: value_repr},
            )

    def _parse_value(self, value: Any) -> ast.expr:
        """Convert a value to an AST expression."""
        if value is None:
            return ast.Constant(value=None)
        if isinstance(value, bool):
            return ast.Constant(value=value)
        if isinstance(value, (int, float)):
            return ast.Constant(value=value)
        if isinstance(value, str):
            # Try to parse as literal
            try:
                # Handle hex strings
                if value.startswith("0x") or value.startswith("0X"):
                    return ast.Constant(value=int(value, 16))
                # Try as number
                if value.replace(".", "").replace("-", "").isdigit():
                    if "." in value:
                        return ast.Constant(value=float(value))
                    else:
                        return ast.Constant(value=int(value))
                # String literal
                return ast.Constant(value=value)
            except ValueError:
                return ast.Constant(value=value)
        # Default: repr as string
        return ast.Constant(value=repr(value))

    def _value_to_repr(self, value: Any) -> str:
        """Convert a value to its Python representation string."""
        if value is None:
            return "None"
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, str):
            # Check if it's a number
            try:
                if value.startswith("0x") or value.startswith("0X"):
                    return str(int(value, 16))
                if "." in value:
                    return str(float(value))
                return str(int(value))
            except ValueError:
                return repr(value)
        return repr(value)

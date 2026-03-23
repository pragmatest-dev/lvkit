"""Code generator for Format String (printf) nodes."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class PrintfCodeGen(NodeCodeGen):
    """Generate code for LabVIEW printf (Format String) nodes.

    LabVIEW's printf takes a format string and arguments, producing
    a formatted output. In Python this becomes string % formatting
    or f-string formatting.
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        inputs = sorted(
            [t for t in node.terminals if t.direction == "input"],
            key=lambda t: t.index,
        )
        outputs = [t for t in node.terminals if t.direction == "output"]

        if not outputs:
            return CodeFragment.empty()

        # Resolve all input values
        input_values = []
        for t in inputs:
            val = ctx.resolve(t.id) or "None"
            input_values.append(val)

        # First input is format string, rest are arguments.
        # Only include as many args as the format string has placeholders.
        if len(input_values) >= 2:
            fmt_str = input_values[0]
            args = input_values[1:]
            # Count format placeholders (%s, %d, %f, etc.)
            import re
            placeholder_count = len(re.findall(r'%[sdfeEgGoxXcr]', fmt_str.strip("'\"")))
            if placeholder_count > 0:
                args = args[:placeholder_count]
            if len(args) == 1:
                expr_str = f"{fmt_str} % {args[0]}"
            else:
                args_str = ", ".join(args)
                expr_str = f"{fmt_str} % ({args_str},)"
        elif len(input_values) == 1:
            expr_str = f"str({input_values[0]})"
        else:
            expr_str = "''"

        statements: list[ast.stmt] = []
        bindings: dict[str, str] = {}

        out_term = outputs[0]
        var_name = to_var_name(out_term.name or "formatted")
        statements.append(build_assign(var_name, parse_expr(expr_str)))
        bindings[out_term.id] = var_name

        return CodeFragment(statements=statements, bindings=bindings)

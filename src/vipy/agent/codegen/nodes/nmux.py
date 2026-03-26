"""Code generator for Node Multiplexer (nMux) nodes."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class NMuxCodeGen(NodeCodeGen):
    """Generate code for LabVIEW nMux (Node Multiplexer).

    nMux selects between N inputs based on a selector index.
    In Python this becomes: result = [val0, val1, ...][selector]
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        # Separate selector input (lowest index input) from value inputs
        inputs = sorted(
            [t for t in node.terminals if t.direction == "input"],
            key=lambda t: t.index,
        )
        outputs = [t for t in node.terminals if t.direction == "output"]

        if not inputs or not outputs:
            return CodeFragment.empty()

        # First input is the selector
        selector_term = inputs[0]
        value_terms = inputs[1:]

        selector_var = ctx.resolve(selector_term.id)
        if not selector_var or selector_var in ("''", '""'):
            selector_var = "0"
        values = []
        for t in value_terms:
            val = ctx.resolve(t.id)
            if val is None:
                # Type-based default for unwired mux input
                ptype = t.python_type() if hasattr(t, 'python_type') else "Any"
                if ptype.startswith("list"):
                    val = "[]"
                elif ptype == "str":
                    val = "''"
                elif ptype == "bool":
                    val = "False"
                elif ptype in ("int", "float"):
                    val = "0"
                else:
                    val = "None"
            values.append(val)

        # Build: result = [val0, val1, ...][selector]
        statements: list[ast.stmt] = []
        bindings: dict[str, str] = {}

        out_term = outputs[0]
        var_name = to_var_name(out_term.name or "mux_result")

        if len(values) == 0:
            # No value inputs — cluster unbundle pass-through
            expr = parse_expr(selector_var)
        elif len(values) == 1:
            # Only one value — just pass through
            expr = parse_expr(values[0])
        else:
            list_expr = ast.Subscript(
                value=ast.List(
                    elts=[parse_expr(v) for v in values],
                    ctx=ast.Load(),
                ),
                slice=parse_expr(selector_var),
                ctx=ast.Load(),
            )
            expr = list_expr

        statements.append(build_assign(var_name, expr))
        bindings[out_term.id] = var_name

        return CodeFragment(statements=statements, bindings=bindings)

"""Code generator for Invoke Nodes (invokeNode).

Invoke nodes call methods on LabVIEW objects (VI Server, ActiveX, .NET).
Generates Python method calls.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class InvokeNodeCodeGen(NodeCodeGen):
    """Generate code for invoke node method calls.

    Produces: result = ref.method_name(args...)
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for an invoke node."""
        method_name = node.method_name or ""
        if not method_name:
            return CodeFragment.empty()

        # Resolve the reference input (first input terminal is typically the object ref)
        ref_var = self._resolve_ref_input(node, ctx)
        method_attr = to_var_name(method_name)

        # Gather non-ref input arguments
        args: list[ast.expr] = []
        for term in node.terminals:
            if term.direction != "input":
                continue
            if term.index == 0:
                continue  # Skip ref input
            if not ctx.is_wired(term.id):
                continue
            value = ctx.resolve(term.id)
            if value is None:
                continue
            args.append(ast.Name(id=value, ctx=ast.Load()))

        # Build method call: ref.method(args...)
        call = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id=ref_var, ctx=ast.Load()),
                attr=method_attr,
                ctx=ast.Load(),
            ),
            args=args,
            keywords=[],
        )

        # Check if there are wired outputs (besides ref passthrough)
        wired_outputs = [
            t for t in node.terminals
            if t.direction == "output" and ctx.is_wired(t.id)
        ]

        statements: list[ast.stmt] = []
        bindings: dict[str, str] = {}

        if wired_outputs:
            # Assign result to variable
            result_var = f"{ref_var}_{method_attr}_result"
            stmt = ast.Assign(
                targets=[ast.Name(id=result_var, ctx=ast.Store())],
                value=call,
            )
            statements.append(stmt)

            # Bind output terminals
            for term in wired_outputs:
                if term.index == 0:
                    # Ref passthrough - bind to same ref variable
                    bindings[term.id] = ref_var
                else:
                    bindings[term.id] = result_var
        else:
            # No outputs wired - just call the method
            statements.append(ast.Expr(value=call))

        return CodeFragment(statements=statements, bindings=bindings)

    def _resolve_ref_input(self, node: Operation, ctx: CodeGenContext) -> str:
        """Resolve the object reference input (typically terminal index 0)."""
        for term in node.terminals:
            if term.direction == "input" and term.index == 0:
                resolved = ctx.resolve(term.id)
                if resolved:
                    return resolved
                # Try tracing through graph to find source
                flow = ctx.get_source(term.id)
                if flow and flow.src_terminal:
                    resolved = ctx.resolve(flow.src_terminal)
                    if resolved:
                        return resolved

        # Fallback: use object_name as variable
        obj_name = node.object_name or "ref"
        return to_var_name(obj_name)

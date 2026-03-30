"""Code generator for Invoke Nodes (invokeNode).

Invoke nodes call methods on LabVIEW objects (VI Server, ActiveX, .NET).
Generates Python method calls.
"""

from __future__ import annotations

import ast

from vipy.graph_types import InvokeOperation

from ..ast_utils import to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import resolve_ref_input


def generate(node: InvokeOperation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an invoke node.

    Produces: result = ref.method_name(args...)
    """
    method_name = node.method_name or ""
    if not method_name:
        return CodeFragment.empty()

    ref_var = resolve_ref_input(node, ctx)
    method_attr = to_var_name(method_name)

    # Gather non-ref input arguments
    args: list[ast.expr] = []
    for term in node.terminals:
        if term.direction != "input":
            continue
        if term.index == 0:
            continue
        if term.is_error_cluster:
            continue
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

    # Check for wired outputs
    wired_outputs = [
        t for t in node.terminals
        if t.direction == "output" and ctx.is_wired(t.id)
    ]

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}

    if wired_outputs:
        result_var = f"{ref_var}_{method_attr}_result"
        stmt = ast.Assign(
            targets=[ast.Name(id=result_var, ctx=ast.Store())],
            value=call,
        )
        statements.append(stmt)

        for term in wired_outputs:
            if term.index == 0:
                bindings[term.id] = ref_var
            else:
                bindings[term.id] = result_var
    else:
        statements.append(ast.Expr(value=call))

    return CodeFragment(statements=statements, bindings=bindings)

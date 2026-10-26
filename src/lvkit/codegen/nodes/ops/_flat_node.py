"""What the flatten / unflatten / swap handlers share: reading a node's
terminals and the statement for a type with no flattened form."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment


def terminal(node: PrimitiveNode, direction: str, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == direction and t.index == index),
        None,
    )


def add_flat_imports(ctx: CodeGenContext, statements: list[ast.stmt]) -> None:
    """Import the modules the flatten / unflatten / swap statements use."""
    module_ast = ast.Module(body=statements, type_ignores=[])
    text = ast.unparse(ast.fix_missing_locations(module_ast))
    for module in ("struct", "ctypes"):
        if f"{module}." in text:
            ctx.add_import(f"import {module}")


def unsupported_fragment(
    node: PrimitiveNode, ctx: CodeGenContext, reason: str
) -> CodeFragment:
    """A statement that raises ``NotImplementedError`` naming the type, with every
    wired output bound to ``None``: the rest of the function still generates."""
    message = f"{node.name or 'This function'} does not handle {reason}"
    raise_statement = ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="NotImplementedError", ctx=ast.Load()),
            args=[ast.Constant(value=message)],
            keywords=[],
        ),
        cause=None,
    )
    statements: list[ast.stmt] = [raise_statement]
    bindings: dict[str, str] = {}
    for term in node.terminals:
        if term.direction == "output" and ctx.is_wired(term.id):
            variable = ctx.make_output_var("unavailable", node.id, terminal_id=term.id)
            statements.append(build_assign(variable, parse_expr("None")))
            bindings[term.id] = variable
    return CodeFragment(statements=statements, bindings=bindings)

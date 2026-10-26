"""What the flatten / unflatten / swap handlers share: reading a node's
terminals and the statement for a type with no flattened form."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ...context import CodeGenContext
from ...errors import CodeGenError


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


def unsupported_layout_error(node: PrimitiveNode, reason: Exception) -> CodeGenError:
    """The error that stops generating a VI whose flatten, unflatten or swap node
    handles a type with no flattened form."""
    return CodeGenError(
        f"{node.name or 'This function'} does not handle {reason}", node
    )

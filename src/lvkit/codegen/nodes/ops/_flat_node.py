"""What the flatten / unflatten / swap handlers share: reading a node's
terminals and constants, and the statement for a type with no flattened form."""

from __future__ import annotations

import ast
from typing import Any

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ...context import CodeGenContext


def terminal(node: PrimitiveNode, direction: str, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == direction and t.index == index),
        None,
    )


def constant_input(
    ctx: CodeGenContext, node: PrimitiveNode, index: int, default: Any
) -> Any:
    """The value of an input that must be a constant, or ``default`` unwired."""
    term = terminal(node, "input", index)
    text = ctx.wired_input(term.id, node) if term else None
    if text is None:
        return default
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as error:
        raise ConversionGap(
            f"{node.name or 'This function'} needs its byte order and size inputs "
            "to be constants",
            node,
        ) from error


def add_flat_imports(ctx: CodeGenContext, statements: list[ast.stmt]) -> None:
    """Import the modules the flatten / unflatten / swap statements use."""
    module_ast = ast.Module(body=statements, type_ignores=[])
    text = ast.unparse(ast.fix_missing_locations(module_ast))
    for module in ("struct", "ctypes"):
        if f"{module}." in text:
            ctx.add_import(f"import {module}")


def unsupported_layout_error(node: PrimitiveNode, reason: Exception) -> ConversionGap:
    """The error that stops generating a VI whose flatten, unflatten or swap node
    handles a type with no flattened form."""
    return ConversionGap(
        f"{node.name or 'This function'} does not handle {reason}", node
    )

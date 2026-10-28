"""What the ops that search a string with a regular expression (Match Pattern,
Search and Replace String) share: reading their inputs, the search start an
``offset`` input gives, and the translator functions written into a module."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...errors import CodeGenError
from ...module_function import function_source
from ._flat_node import terminal


def input_expression(
    ctx: CodeGenContext, node: PrimitiveNode, index: int
) -> str | None:
    """The expression feeding input ``index``; None when it is unwired."""
    term = terminal(node, "input", index)
    if term is None:
        return None
    text = ctx.resolve(term.id)
    if text is None and ctx.is_wired(term.id):
        text = ctx.resolve(term.id, allow_none=True)
        if text is None:
            raise CodeGenError(f"Unresolved wired {node.name} input {index}", node)
    return text


def literal(text: str | None) -> object | None:
    """The value of an expression that is a literal; None for anything else."""
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


def is_wired_output(ctx: CodeGenContext, node: PrimitiveNode, index: int) -> bool:
    term = terminal(node, "output", index)
    return term is not None and ctx.is_wired(term.id)


def string_variable(
    ctx: CodeGenContext, node: PrimitiveNode, text: str, statements: list[ast.stmt]
) -> str:
    """``text`` as a name: itself when it is one, else a variable assigned it."""
    if text.isidentifier():
        return text
    name = ctx.make_output_var("string", node.id)
    statements.append(build_assign(name, parse_expr(text)))
    return name


def search_start(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    offset: str | None,
    statements: list[ast.stmt],
) -> str | None:
    """The name of the index the search starts at, assigned in ``statements``;
    None when it starts at 0 (an unwired or constant offset of 0 or less). A
    negative offset counts as 0."""
    value = literal(offset) if offset is not None else 0
    if isinstance(value, int) and not isinstance(value, bool):
        start = str(max(value, 0))
    else:
        start = f"max({offset}, 0)"
    if start == "0":
        return None
    name = ctx.make_output_var("search_start", node.id)
    statements.append(build_assign(name, parse_expr(start)))
    return name


def declare_translator(
    ctx: CodeGenContext, name: str, function: Callable[..., Any]
) -> str:
    """Write ``function`` into the generated module as ``name`` (once, whatever
    the number of nodes using it); the name it is declared under."""
    return ctx.ctypes_module.declare(
        ("helper", name), name, lambda declared: function_source(function, declared)
    )

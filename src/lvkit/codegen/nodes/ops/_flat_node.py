"""What the flatten / unflatten / swap handlers share: reading a node's
terminals and constants, and the statement for a type with no flattened form."""

from __future__ import annotations

import ast
from typing import Any

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import Functions, Integer, Layout, Real


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


def read_flattened(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    layout: Layout,
    text: str,
    order: str,
    prefix: bool,
    value_term: Terminal | None,
    wants_value: bool,
) -> tuple[list[ast.stmt], str | None, str]:
    """The statements that read a value of ``layout`` from the flattened string
    ``text``: they return the variable holding the value (None when neither the
    caller wants it nor reading the rest needs it) and the expression of the bytes
    left after it."""
    fns = Functions(ctx, order)
    data = ctx.make_output_var("flattened", node.id)
    statements: list[ast.stmt] = [
        build_assign(data, parse_expr(f"{text}.encode('latin-1')"))
    ]
    if isinstance(layout, (Integer, Real)):
        read = f"struct.unpack_from({order + layout.code!r}, {data})"
        value = f"complex(*{read})" if len(layout.code) == 2 else f"{read}[0]"
        rest = f"{data}[{layout.width}:]"
        position = None
    else:
        position = ctx.make_output_var("position", node.id)
        value = None
        rest = f"{data}[{position}:]"
    variable = None
    if wants_value or position is not None:
        assert value_term is not None
        variable = ctx.make_output_var("value", node.id, terminal_id=value_term.id)
        if position is None:
            statements.append(build_assign(variable, parse_expr(value or "None")))
        else:
            statements.append(
                ast.parse(
                    f"{variable}, {position} = "
                    f"{fns.reader(layout, prefix)}({data}, 0)"
                ).body[0]
            )
    return statements, variable, rest

"""Code generator for compound operations (cpdArith, aBuild)."""

from __future__ import annotations

import ast

from lvpy.models import PrimitiveOperation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate_compound_arith(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for compound arithmetic (cpdArith).

    Combines multiple inputs with a single operation (OR, AND, ADD).
    """
    terminals = node.terminals
    operation = node.operation or "or"

    inputs = [t for t in terminals if t.direction == "input"]
    outputs = [t for t in terminals if t.direction == "output"]

    if not outputs:
        return CodeFragment()

    output_term = outputs[0]
    output_id = output_term.id

    input_exprs = []
    input_names = []
    for inp in sorted(inputs, key=lambda t: t.index):
        val = ctx.resolve(inp.id)
        if val:
            input_exprs.append(val)
            input_names.append(val)

    var_name = _make_arith_var_name(operation, input_names)

    if not input_exprs:
        default_value = False if operation in ("or", "and") else 0
        stmt = build_assign(var_name, ast.Constant(value=default_value))
        return CodeFragment(
            statements=[stmt],
            bindings={output_id: var_name},
        )

    if len(input_exprs) == 1:
        return CodeFragment(bindings={output_id: input_exprs[0]})

    combined = parse_expr(input_exprs[0])

    if operation == "or":
        for expr_str in input_exprs[1:]:
            combined = ast.BoolOp(
                op=ast.Or(),
                values=[combined, parse_expr(expr_str)],
            )
    elif operation == "and":
        for expr_str in input_exprs[1:]:
            combined = ast.BoolOp(
                op=ast.And(),
                values=[combined, parse_expr(expr_str)],
            )
    elif operation == "add":
        for expr_str in input_exprs[1:]:
            combined = ast.BinOp(
                left=combined,
                op=ast.Add(),
                right=parse_expr(expr_str),
            )
    else:
        for expr_str in input_exprs[1:]:
            combined = ast.BoolOp(
                op=ast.Or(),
                values=[combined, parse_expr(expr_str)],
            )

    stmt = build_assign(var_name, combined)
    return CodeFragment(
        statements=[stmt],
        bindings={output_id: var_name},
    )


def _make_arith_var_name(operation: str, input_names: list[str]) -> str:
    """Generate a semantic variable name for compound arithmetic."""
    if operation in ("or", "and"):
        stop_keywords = {
            "stop", "done", "exit", "quit", "end", "finish", "complete"
        }
        for name in input_names:
            if any(kw in name.lower() for kw in stop_keywords):
                return "should_stop"
        return "should_stop"

    if operation == "add" and input_names:
        return "total"

    return "combined"


def generate_array_build(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Generate code for array building (aBuild)."""
    terminals = node.terminals

    inputs = [t for t in terminals if t.direction == "input"]
    outputs = [t for t in terminals if t.direction == "output"]

    if not outputs:
        return CodeFragment()

    output_term = outputs[0]
    output_id = output_term.id

    elements = []
    input_names = []
    for inp in sorted(inputs, key=lambda t: t.index):
        val = ctx.resolve(inp.id)
        if val:
            elements.append(parse_expr(val))
            input_names.append(val)
        else:
            elements.append(ast.Constant(value=None))

    var_name = _make_array_var_name(input_names)
    list_expr = ast.List(elts=elements, ctx=ast.Load())
    stmt = build_assign(var_name, list_expr)

    return CodeFragment(
        statements=[stmt],
        bindings={output_id: var_name},
    )


def _make_array_var_name(input_names: list[str]) -> str:
    """Generate a semantic variable name for array building."""
    if not input_names:
        return "items"

    first = input_names[0]
    base = to_var_name(first).rstrip("0123456789_")

    if base and len(base) > 2:
        common = all(base in to_var_name(n) for n in input_names[:3])
        if common:
            if base.endswith("y") and not base.endswith(("ay", "ey", "oy", "uy")):
                return base[:-1] + "ies"
            elif base.endswith(("s", "x", "z", "ch", "sh")):
                return base + "es"
            else:
                return base + "s"

    return "items"

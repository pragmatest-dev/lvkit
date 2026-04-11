"""Build AST condition expressions from LabVIEW dataflow.

This module traces backward from a stop terminal to build compound boolean
expressions. It handles comparison primitives and boolean operations.
"""

from __future__ import annotations

import ast

from lvpy.models import Operation, PrimitiveOperation

from .ast_utils import parse_expr
from .context import CodeGenContext

# Mapping of primitive IDs to AST comparison operators
COMPARISON_PRIMITIVES: dict[int, type[ast.cmpop]] = {
    1102: ast.Eq,       # Equal?
    1103: ast.GtE,      # Greater Or Equal?
    1105: ast.NotEq,    # Not Equal?
    1107: ast.Lt,       # Less? (inferred)
    1108: ast.LtE,      # Less Or Equal?
    1110: ast.Gt,       # Greater?
}

# Mapping of primitive IDs to AST boolean operators
BOOLEAN_PRIMITIVES: dict[int, type[ast.boolop]] = {
    1100: ast.And,      # And
    1101: ast.Or,       # Or
}

# Not primitive for unary negation
NOT_PRIMITIVES: set[int] = {1109}  # Not


def build_condition_expr(
    stop_terminal: str,
    ctx: CodeGenContext,
    inner_ops: list[Operation],
) -> ast.expr | None:
    """Build AST expression for a while loop stop condition.

    Traces backward from the stop terminal through inner loop operations
    to find comparison or boolean primitives and builds an AST expression.

    Args:
        stop_terminal: Terminal UID that receives the stop condition boolean
        ctx: Code generation context with bindings and data flow
        inner_ops: Operations inside the loop

    Returns:
        AST expression node or None if can't build a compound expression
    """
    # Build lookup map: terminal_uid -> Operation that outputs to it
    output_to_op: dict[str, Operation] = {}
    for op in inner_ops:
        for term in op.terminals:
            if term.direction == "output":
                output_to_op[term.id] = op

    # Find what flows into the stop terminal
    source_terminal = _trace_source(stop_terminal, ctx)
    if not source_terminal:
        return None

    # Check if the source terminal is an output of an operation
    source_op = output_to_op.get(source_terminal)
    if not source_op:
        return None

    # Try to build expression from the source operation
    return _build_expr_from_op(source_op, ctx, output_to_op)


def _trace_source(terminal_uid: str, ctx: CodeGenContext) -> str | None:
    """Trace backward to find the source terminal feeding a destination.

    Args:
        terminal_uid: Destination terminal UID
        ctx: Code generation context

    Returns:
        Source terminal UID or None
    """
    flow_info = ctx.get_source(terminal_uid)
    if flow_info:
        return flow_info.src_terminal
    return None


def _build_expr_from_op(
    op: Operation,
    ctx: CodeGenContext,
    output_to_op: dict[str, Operation],
) -> ast.expr | None:
    """Build AST expression from an operation.

    Handles comparison primitives, boolean operations, and recursion.

    Args:
        op: Operation to convert
        ctx: Code generation context
        output_to_op: Map from output terminal UIDs to operations

    Returns:
        AST expression or None
    """
    # Check for compound arithmetic (cpdArith) first - these have no primResID
    # but we can still build expressions from them
    if (
        isinstance(op, PrimitiveOperation)
        and op.node_type == "cpdArith"
        and op.operation
    ):
        return _build_cpd_arith(op, ctx, output_to_op)

    prim_id = op.primResID if isinstance(op, PrimitiveOperation) else None
    if prim_id is None:
        return None

    # Check for comparison primitive
    if prim_id in COMPARISON_PRIMITIVES:
        return _build_comparison(op, prim_id, ctx)

    # Check for boolean AND/OR
    if prim_id in BOOLEAN_PRIMITIVES:
        return _build_boolean_op(op, prim_id, ctx, output_to_op)

    # Check for NOT
    if prim_id in NOT_PRIMITIVES:
        return _build_not(op, ctx, output_to_op)

    return None


def _build_comparison(
    op: Operation,
    prim_id: int,
    ctx: CodeGenContext,
) -> ast.expr | None:
    """Build AST Compare node from comparison primitive.

    Args:
        op: Comparison operation
        prim_id: Primitive ID
        ctx: Code generation context

    Returns:
        AST Compare expression or None
    """
    cmp_op = COMPARISON_PRIMITIVES.get(prim_id)
    if not cmp_op:
        return None

    # Get input terminals (typically index 1=x, 2=y for comparisons)
    inputs = [t for t in op.terminals if t.direction == "input"]
    inputs.sort(key=lambda t: t.index)

    if len(inputs) < 2:
        return None

    # Resolve input values
    left_val = ctx.resolve(inputs[0].id)
    right_val = ctx.resolve(inputs[1].id)

    if not left_val or not right_val:
        return None

    # Build Compare AST
    return ast.Compare(
        left=parse_expr(left_val),
        ops=[cmp_op()],
        comparators=[parse_expr(right_val)],
    )


def _build_boolean_op(
    op: Operation,
    prim_id: int,
    ctx: CodeGenContext,
    output_to_op: dict[str, Operation],
) -> ast.expr | None:
    """Build AST BoolOp node from boolean primitive.

    Args:
        op: Boolean operation (AND/OR)
        prim_id: Primitive ID
        ctx: Code generation context
        output_to_op: Map for recursive resolution

    Returns:
        AST BoolOp expression or None
    """
    bool_op = BOOLEAN_PRIMITIVES.get(prim_id)
    if not bool_op:
        return None

    inputs = [t for t in op.terminals if t.direction == "input"]
    inputs.sort(key=lambda t: t.index)

    if len(inputs) < 2:
        return None

    # Try to recursively build expressions for each input
    values: list[ast.expr] = []
    for inp in inputs:
        # Check if this input comes from another operation we can expand
        source = _trace_source(inp.id, ctx)
        if source and source in output_to_op:
            nested_expr = _build_expr_from_op(output_to_op[source], ctx, output_to_op)
            if nested_expr:
                values.append(nested_expr)
                continue

        # Fall back to resolved variable
        resolved = ctx.resolve(inp.id)
        if resolved:
            values.append(parse_expr(resolved))

    if len(values) < 2:
        return None

    return ast.BoolOp(op=bool_op(), values=values)


def _build_not(
    op: Operation,
    ctx: CodeGenContext,
    output_to_op: dict[str, Operation],
) -> ast.expr | None:
    """Build AST UnaryOp (Not) from NOT primitive.

    Args:
        op: NOT operation
        ctx: Code generation context
        output_to_op: Map for recursive resolution

    Returns:
        AST UnaryOp expression or None
    """
    inputs = [t for t in op.terminals if t.direction == "input"]
    if not inputs:
        return None

    inp = inputs[0]

    # Try recursive expansion
    source = _trace_source(inp.id, ctx)
    if source and source in output_to_op:
        nested_expr = _build_expr_from_op(output_to_op[source], ctx, output_to_op)
        if nested_expr:
            return ast.UnaryOp(op=ast.Not(), operand=nested_expr)

    # Fall back to resolved variable
    resolved = ctx.resolve(inp.id)
    if resolved:
        return ast.UnaryOp(op=ast.Not(), operand=parse_expr(resolved))

    return None


def _build_cpd_arith(
    op: PrimitiveOperation,
    ctx: CodeGenContext,
    output_to_op: dict[str, Operation],
) -> ast.expr | None:
    """Build AST expression from compound arithmetic node.

    Compound arithmetic (cpdArith) is used for OR/AND of multiple booleans.
    Recursively expands inputs from other operations (comparisons, NOT, etc.).

    Args:
        op: Compound arithmetic operation
        ctx: Code generation context
        output_to_op: Map from output terminal UIDs to operations

    Returns:
        AST BoolOp expression or None
    """
    # Map operation name to AST operator
    op_map = {
        "or": ast.Or,
        "and": ast.And,
    }

    operation = op.operation.lower() if op.operation else ""
    bool_op = op_map.get(operation)
    if not bool_op:
        return None

    inputs = [t for t in op.terminals if t.direction == "input"]
    inputs.sort(key=lambda t: t.index)

    if len(inputs) < 2:
        return None

    # Try to recursively build expressions for each input
    values: list[ast.expr] = []
    for inp in inputs:
        # Check if this input comes from another operation we can expand
        source = _trace_source(inp.id, ctx)
        if source and source in output_to_op:
            nested_expr = _build_expr_from_op(output_to_op[source], ctx, output_to_op)
            if nested_expr:
                values.append(nested_expr)
                continue

        # Fall back to resolved variable
        resolved = ctx.resolve(inp.id)
        if resolved:
            values.append(parse_expr(resolved))

    if len(values) < 2:
        return None

    return ast.BoolOp(op=bool_op(), values=values)

"""Reading the terminals of a call of a gRPC library VI by the names its catalog
entry gives them (``python_param``), shared by the gRPC node modules."""

from __future__ import annotations

import ast

from lvkit.graph.models import VINode
from lvkit.models import Terminal, TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..context import CodeGenContext

# The grpcId passed through by a library VI that does not change it.
PASSTHROUGH_IN = "grpcid_in"
PASSTHROUGH_OUT = "grpcid_out"


def index_of(entry: VIEntry, param: str, direction: TerminalDirection) -> int | None:
    """The connector-pane index of the catalog terminal ``param``."""
    return next(
        (
            t.index
            for t in entry.terminals
            if t.python_param == param and t.direction == direction
        ),
        None,
    )


def terminal_of(
    node: VINode, entry: VIEntry, param: str, direction: TerminalDirection
) -> Terminal | None:
    """The call's terminal of the catalog terminal ``param``; None when the
    call does not record it."""
    index = index_of(entry, param, direction)
    return next(
        (t for t in node.terminals if t.index == index and t.direction == direction),
        None,
    )


def wired(node: VINode, entry: VIEntry, param: str, ctx: CodeGenContext) -> str | None:
    """The expression wired to the input ``param``; None when it is unwired."""
    term = terminal_of(node, entry, param, TerminalDirection.INPUT)
    return (ctx.wired_input(term.id, node) or None) if term is not None else None


def constant_text(expression: str | None) -> str | None:
    """The string a wired expression is, when the input is a constant."""
    try:
        value = ast.literal_eval(expression) if expression else None
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def bind_passthrough(
    node: VINode, entry: VIEntry, ctx: CodeGenContext, target: dict[str, str]
) -> None:
    """Bind the grpcId output to the grpcId input in ``target`` (terminal id to
    expression)."""
    out = terminal_of(node, entry, PASSTHROUGH_OUT, TerminalDirection.OUTPUT)
    if out is not None and ctx.is_wired(out.id):
        target[out.id] = wired(node, entry, PASSTHROUGH_IN, ctx) or "None"

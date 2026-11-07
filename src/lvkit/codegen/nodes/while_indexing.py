"""Auto-indexing input tunnels of a While Loop.

A While Loop indexes an array wired to an auto-indexing input tunnel the way a
For Loop does: iteration ``i`` reads element ``i``. The array does not limit
how many times the loop runs; once its elements are used up, the tunnel gives
the default value of the element type (NI, "Processing Individual Elements in
an Array or a Collection with a Loop"). So each such tunnel is a local set at
the top of each iteration: ``element = array[i] if i < len(array) else
<default>``.
"""

from __future__ import annotations

import ast

from lvkit.models import Terminal, Tunnel, TunnelMode

from ..ast_utils import build_assign, parse_expr, type_default_expr
from ..context import CodeGenContext


def indexing_inputs(tunnels: list[Tunnel], inputs: set[str]) -> list[Tunnel]:
    """The auto-indexing input tunnels among ``tunnels`` (``inputs`` holds the
    outer terminal ids that are inputs)."""
    return [
        t
        for t in tunnels
        if t.tunnel_type == "lpTun"
        and t.mode == TunnelMode.INDEXING
        and t.outer_terminal_uid in inputs
        and t.inner_terminal_uid
    ]


def bind_elements(
    tunnels: list[Tunnel],
    term_by_id: dict[str, Terminal],
    counter: str,
    ctx: CodeGenContext,
    inner_ctx: CodeGenContext,
    loop_id: str,
) -> list[ast.stmt]:
    """Bind the inner terminal of each auto-indexing input tunnel to a local
    holding element ``counter`` of the array wired to it, and return the
    statements that set those locals at the top of each iteration."""
    statements: list[ast.stmt] = []
    for tunnel in tunnels:
        assert tunnel.outer_terminal_uid and tunnel.inner_terminal_uid
        array = ctx.resolve(tunnel.outer_terminal_uid)
        if array is None:
            continue
        inner = term_by_id.get(tunnel.inner_terminal_uid)
        default = type_default_expr(inner.lv_type if inner is not None else None)
        element = ctx.make_output_var("element", loop_id, tunnel.inner_terminal_uid)
        statements.append(
            build_assign(
                element,
                parse_expr(
                    f"({array})[{counter}] if {counter} < len({array}) "
                    f"else {ast.unparse(default)}"
                ),
            )
        )
        inner_ctx.bind(tunnel.inner_terminal_uid, element)
    return statements

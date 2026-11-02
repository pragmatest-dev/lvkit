"""Where the value of a node's output goes: straight into the one terminal that
reads it, or into a variable named after its wire.

A value is written into its reader when exactly one terminal reads it and that
terminal reads it on the node's own diagram (the same frame of the same
structure, ``InMemoryVIGraph.read_in_frame_of``), so the value is still computed
once and in the frame it was computed in: a value that enters a loop, case frame
or sequence frame through a tunnel (or a shift register, the case selector's
inside face, the count terminal's inside face) would be computed each time that
diagram runs, and an indicator placed inside a frame is read where the VI
returns, outside it. A value whose expression calls a function is always kept in
a variable: the call may have effects, and moving it would change when they
happen.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.graph.models import LoopNode
from lvkit.models import TunnelTerminal

from .ast_utils import build_assign, paren_if_compound
from .fragment import CodeFragment

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode, WireEnd

    from .context import CodeGenContext


def has_call(expr: ast.expr) -> bool:
    """Whether the expression calls a function anywhere in it."""
    return any(isinstance(part, ast.Call) for part in ast.walk(expr))


def written_into_reader(
    node: AnyGraphNode, terminal_id: str, expr: ast.expr, ctx: CodeGenContext
) -> bool:
    """Whether the output ``terminal_id`` of ``node``, whose value is ``expr``,
    is written straight into the one terminal that reads it (see the module
    docstring).
    ``None`` stays in a variable: on a terminal it marks a constant that is a
    gap. So does a value that reads a variable set again before its reader runs
    (``_set_again_before``)."""
    if ctx.graph is None or has_call(expr):
        return False
    if isinstance(expr, ast.Constant) and expr.value is None:
        return False
    readers = ctx.graph.wire_readers(terminal_id)
    if len(readers) != 1 or not ctx.graph.read_in_frame_of(readers[0], node):
        return False
    set_again = _set_again_before(readers[0], ctx)
    return not any(
        isinstance(part, ast.Name) and part.id in set_again for part in ast.walk(expr)
    )


def _set_again_before(reader: WireEnd, ctx: CodeGenContext) -> set[str]:
    """The variables that may be set between a node and the terminal ``reader``
    on its diagram: the variable of an indicator a Local Variable reads (any
    write of the indicator), and, for a loop's conditional terminal or the
    inside face of its output tunnel, the loop's shift registers, which take
    their next values before the loop tests its conditional terminal and before
    a While Loop's tunnel is read after the loop."""
    names = set(ctx.local_indicators.variables.values())
    assert ctx.graph is not None
    loop = ctx.graph.get_graph_node(reader.node_id)
    if not isinstance(loop, LoopNode):
        return names
    face = ctx.graph.get_terminal(reader.terminal_id)
    if reader.terminal_id == loop.stop_condition_terminal or (
        isinstance(face, TunnelTerminal)
        and face.tunnel_type == "lpTun"
        and face.boundary == "inner"
    ):
        for term in loop.terminals:
            if (
                isinstance(term, TunnelTerminal)
                and term.tunnel_type == "lSR"
                and term.boundary == "inner"
                and (name := ctx.graph.get_var_name(term.id))
            ):
                names.add(name)
    return names


def bind_output(
    node: AnyGraphNode,
    terminal_id: str,
    base_name: str,
    expr: ast.expr,
    ctx: CodeGenContext,
    *,
    movable: bool = True,
) -> CodeFragment:
    """The value ``expr`` of the output ``terminal_id`` of ``node``: bound to
    the expression itself when it is written into its reader, else assigned to
    a variable named from ``base_name`` (``ctx.make_output_var``, which names a
    value that leaves a structure after its tunnel). ``movable`` is False when
    the expression reads names the node's own statements set just before it,
    which a later node may set again; the value then stays in a variable."""
    if movable and written_into_reader(node, terminal_id, expr, ctx):
        return CodeFragment(
            bindings={terminal_id: paren_if_compound(ast.unparse(expr))}
        )
    var = ctx.make_output_var(base_name, node.id, terminal_id=terminal_id)
    return CodeFragment(
        statements=[build_assign(var, expr)], bindings={terminal_id: var}
    )

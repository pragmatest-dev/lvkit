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
happen. So is a value read from an array, cluster or class that another node may
change in place before the reader runs (``_changed_before_read``).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.graph.models import LoopNode
from lvkit.models import LVTypeKind, Terminal, TunnelTerminal

from .ast_utils import build_assign, paren_if_compound, parse_expr
from .fragment import CodeFragment

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode, WireEnd

    from .context import CodeGenContext

_CHANGEABLE_KINDS = (
    LVTypeKind.ARRAY,
    LVTypeKind.CLUSTER,
    LVTypeKind.CLASS,
    LVTypeKind.TYPEDEF_REF,
)


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
    if len(readers) != 1:
        return False
    # An indicator held in a variable is set right after the node, where it is.
    held = readers[0].terminal_id in ctx.local_indicators.variables
    if not held and not ctx.graph.read_in_frame_of(readers[0], node):
        return False
    read = _names(expr)
    if read & _set_again_before(readers[0], ctx):
        return False
    return not _changed_before_read(node, terminal_id, read, ctx)


def _names(expr: ast.expr) -> set[str]:
    """The variables the expression reads."""
    return {part.id for part in ast.walk(expr) if isinstance(part, ast.Name)}


def _changeable(term: Terminal) -> bool:
    """Whether the terminal carries an array, cluster or class object, which a
    node such as Bundle By Name or an In Place Element Structure may change in
    place. An error cluster is not one: every change to an error makes a new
    error value (``nodes/nmux._generate_error_bundle``)."""
    lv_type = term.lv_type
    return lv_type is not None and not term.is_error_cluster and (
        lv_type.kind in _CHANGEABLE_KINDS or lv_type.ref_type == "UDClassInst"
    )


def _changed_before_read(
    node: AnyGraphNode, terminal_id: str, read: set[str], ctx: CodeGenContext
) -> bool:
    """Whether a value the expression of output ``terminal_id`` reads through an
    input of ``node`` could be changed in place before the expression's reader
    runs. A node that changes a value in place copies it only when something
    that does not run before it can still read it
    (``InMemoryVIGraph.value_is_shared``); the node's own read counts as one
    that ran first, which stops being true when the read moves to its reader.
    So the value stays in a variable when the expression reads an array, cluster
    or class input whose wire also goes to another terminal
    (``InMemoryVIGraph.value_read_elsewhere``), or that another output of the
    node can pass on."""
    assert ctx.graph is not None
    passes_on = any(
        term.direction == "output"
        and term.id != terminal_id
        and _changeable(term)
        and ctx.is_wired(term.id)
        for term in node.terminals
    )
    for term in node.terminals:
        if term.direction != "input" or not _changeable(term):
            continue
        value = ctx.resolve(term.id)
        if value is None or not (read & _names(parse_expr(value))):
            continue
        if passes_on or ctx.graph.value_read_elsewhere(term.id):
            return True
    return False


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

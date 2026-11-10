"""A Local Variable of a control, and a reference that names no front-panel control.

A Local Variable of a control of the VI is its variable (the control's
parameter): a write assigns it, a read reads it. In a VI that has the hooks
(``panel_hooks``) a write is followed by ``on_update``, and a read inside a loop
or Event Structure is preceded by ``on_read``.

"This VI" is the VI being generated, and a VI is its function: the reference is
that function (``CodeGenContext.this_vi_expression``), so a subVI that is passed
it receives the function and ``ref.__name__`` / ``inspect.getfile(ref)`` read its
properties.
Any other such reference ("This Application") is a LabVIEW server object with no
Python value to read: a gap, its outputs ``None`` where consumers read them."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, LocalVariableNode
from lvkit.graph.vi_reference import is_this_vi
from lvkit.models import FPTerminal

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panel_hooks import read_stmt, update_stmt
from ..references import asks_for_control, referenced_control


def generate(node: LocalVariableNode, ctx: CodeGenContext) -> CodeFragment:
    if is_this_vi(node):
        return CodeFragment(bindings={node.terminals[0].id: ctx.this_vi_expression()})
    control = referenced_control(node, ctx)
    if control is not None:
        return _control_access(node, control, ctx)
    raise ConversionGap(
        f"The reference {node.display_name!r} names no front-panel control, so "
        "there is no value to read",
        node,
    )


def _control_access(
    node: LocalVariableNode, control: FPTerminal, ctx: CodeGenContext
) -> CodeFragment:
    """The write of the control's variable, or the read of it."""
    variable = ctx.resolve(control.id)
    if variable is None:
        raise ConversionGap(
            f"The control {control.name!r} has no variable to "
            f"{'write' if node.is_write else 'read'}",
            node,
        )
    terminal = node.terminals[0].id
    if node.is_write:
        value = ctx.resolve(terminal)
        if value is None:
            raise ConversionGap(
                f"The value written to the Local Variable of {control.name!r} "
                "has no source lvkit can read",
                node,
            )
        return CodeFragment(statements=write_control(control, variable, value, ctx))
    return CodeFragment(
        statements=read_control(node, control, variable, ctx),
        bindings={terminal: variable},
    )


def write_control(
    control: FPTerminal, variable: str, value: str, ctx: CodeGenContext
) -> list[ast.stmt]:
    """``variable = value``, then ``on_update`` in a VI that has the hooks: the
    statements that write the control ``control`` (whose variable is
    ``variable``)."""
    hooks = ctx.panel_hooks
    statements: list[ast.stmt] = []
    if value != variable:
        statements.append(build_assign(variable, parse_expr(value)))
    if hooks is not None and control.id in hooks.names:
        statements.append(update_stmt(hooks, control.id, variable))
    return statements


def read_control(
    node: AnyGraphNode, control: FPTerminal, variable: str, ctx: CodeGenContext
) -> list[ast.stmt]:
    """The statements that read the control ``control`` at ``node``: ``on_read``
    when the node asks the caller for the value (``asks_for_control``)."""
    hooks = ctx.panel_hooks
    if hooks is not None and asks_for_control(node, control, ctx):
        return [read_stmt(hooks, control.id, variable)]
    return []

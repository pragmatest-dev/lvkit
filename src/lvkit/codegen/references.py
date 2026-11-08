"""Local variables and control references in the nodes code generation walks.

A control is a variable of the function (its parameter), so a Local Variable of
a control is an assignment (a write) or a read of that variable. A write is a
node of the walk. A read is a node of the walk when it asks the caller for the
control's current value (``on_read``, ``panel_hooks``), which it does inside a
loop or Event Structure of a VI that has the hooks, or when the VI also writes
the control by a Local Variable (so the read gives the variable where its node
runs); every other read resolves through the wire to the control's parameter,
generates nothing and is left out of the walk. A Local Variable of an indicator
is left out of the walk: the
indicator's variable is assigned where its value is produced
(``local_indicators``). A reference that names no front-panel control ("This
Application") has no value to read, so it stays in the walk and is a gap of its
own node, whose consumers read its outputs as ``None``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode, LocalVariableNode
from lvkit.models import FPTerminal

if TYPE_CHECKING:
    from .context import CodeGenContext

# The node kinds the graph walk is widened by to see these references.
REFERENCE_KINDS = ("local_variable",)


def is_unresolved_reference(node: AnyGraphNode) -> bool:
    """Whether ``node`` reads a reference that names no front-panel control."""
    return (
        isinstance(node, LocalVariableNode)
        and not node.is_write
        and node.control_terminal_id is None
    )


def referenced_control(
    node: LocalVariableNode, ctx: CodeGenContext
) -> FPTerminal | None:
    """The control of the VI that the Local Variable ``node`` reads or writes;
    None for an indicator or a reference that names no control."""
    if ctx.graph is None or node.control_terminal_id is None:
        return None
    control = ctx.graph.get_terminal(node.control_terminal_id)
    if isinstance(control, FPTerminal) and not control.is_indicator:
        return control
    return None


def asks_for_control(
    node: LocalVariableNode, control: FPTerminal, ctx: CodeGenContext
) -> bool:
    """Whether the read ``node`` of ``control`` asks the caller for its value: the
    VI has the hooks and the read is inside a loop or Event Structure."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    return (
        hooks is not None
        and graph is not None
        and control.id in hooks.names
        and graph.enclosing_loop(node.parent) is not None
    )


def generated_nodes(
    nodes: list[AnyGraphNode], ctx: CodeGenContext
) -> list[AnyGraphNode]:
    """``nodes`` without the local variables that generate nothing."""
    return [node for node in nodes if _generates(node, ctx)]


def _generates(node: AnyGraphNode, ctx: CodeGenContext) -> bool:
    if not isinstance(node, LocalVariableNode) or is_unresolved_reference(node):
        return True
    control = referenced_control(node, ctx)
    if control is None or ctx.graph is None:
        return False
    return (
        node.is_write
        or asks_for_control(node, control, ctx)
        or ctx.graph.written_by_local_variable(control.id)
    )


def is_walked_read(terminal_id: str, ctx: CodeGenContext) -> bool:
    """Whether ``terminal_id`` is the output of a Local Variable read of a control
    that is a node of the walk: its consumers get the control's variable where
    the node runs, not before."""
    if ctx.graph is None:
        return False
    node = ctx.graph.get_graph_node(ctx.graph.terminal_node_id(terminal_id))
    return (
        isinstance(node, LocalVariableNode)
        and not node.is_write
        and referenced_control(node, ctx) is not None
        and _generates(node, ctx)
    )

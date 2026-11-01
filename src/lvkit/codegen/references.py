"""Local variables and control references in the nodes code generation walks.

A read of a front-panel control resolves through the wire to the control's
parameter, so it generates nothing and is left out of the walk. A reference that
names no front-panel control ("This Application") has no value to read, so it
stays in the walk and is a gap of its own node, whose consumers read its outputs
as ``None``."""

from __future__ import annotations

from lvkit.graph.models import AnyGraphNode, LocalVariableNode

# The node kinds the graph walk is widened by to see these references.
REFERENCE_KINDS = ("local_variable",)


def is_unresolved_reference(node: AnyGraphNode) -> bool:
    """Whether ``node`` reads a reference that names no front-panel control."""
    return (
        isinstance(node, LocalVariableNode)
        and not node.is_write
        and node.control_terminal_id is None
    )


def generated_nodes(nodes: list[AnyGraphNode]) -> list[AnyGraphNode]:
    """``nodes`` without the local variables that generate nothing."""
    return [
        node
        for node in nodes
        if not isinstance(node, LocalVariableNode) or is_unresolved_reference(node)
    ]

"""A node's terminals by catalog role, placed for the arrays wired to it."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import LaidOutTerminal, lay_out_node
from lvkit.models import Terminal
from lvkit.primitive_resolver import get_resolver

from ..errors import CodeGenError


def laid_out_roles(
    node: PrimitiveNode, node_type: str
) -> dict[int, list[tuple[LaidOutTerminal, Terminal]]]:
    """The node's terminals by catalog slot, placed for its array types.

    Each slot lists its terminals in index order with their position in the
    node's repeated sets and dimensions (see ``graph/terminal_layout.py``).
    """
    template = get_resolver().resolve_by_node_type(node_type)
    if template is None:
        raise ConversionGap(
            f"No terminal layout is known for {node.name or 'this function'}", node
        )
    laid_out = lay_out_node(template.terminals, node.terminals)
    if laid_out is None:
        raise ConversionGap(
            f"{template.name} terminals do not fit the array types wired to it "
            "(unknown array dimensions, or a terminal count that does not make "
            "whole sets).",
            node,
        )
    by_index = {t.index: t for t in node.terminals}
    roles: dict[int, list[tuple[LaidOutTerminal, Terminal]]] = {}
    for placed in sorted(laid_out, key=lambda p: p.index):
        terminal = by_index.get(placed.index)
        if terminal is None:
            raise CodeGenError(
                f"{template.name} has no terminal at index {placed.index}", node
            )
        roles.setdefault(placed.slot, []).append((placed, terminal))
    return roles

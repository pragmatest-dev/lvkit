"""Where a variant wire starts: whether it is the empty Variant.

LabVIEW's "variant as lookup table" (an empty variant given attributes) is a
Python ``dict`` of the attributes, decided at generation time by this query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Terminal, TunnelTerminal
from .models import AnyGraphNode, ConstantNode, PrimitiveNode, StructureNode, WireEnd
from .shift_registers import pair_by_position

# The primitives whose variant output is the variant at their variant input
# (index 7) with attributes set or deleted: Set Variant Attribute and Delete
# Variant Attribute. Their variant out is the variant out (index 3).
_ATTRIBUTE_EDITORS = frozenset({8204, 8206})
_VARIANT_IN = 7
_VARIANT_OUT = 3


class VariantSourceMixin:
    """The ``variant_is_empty`` query on ``InMemoryVIGraph``."""

    if TYPE_CHECKING:

        def get_graph_node(self, node_id: str) -> AnyGraphNode | None: ...
        def get_terminal(self, terminal_id: str) -> Terminal | None: ...
        def incoming_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def terminal_vi(self, terminal_id: str) -> str | None: ...
        def terminal_node_id(self, terminal_id: str) -> str: ...

    def variant_is_empty(self, terminal_id: str) -> bool:
        """Whether the variant that reaches the input ``terminal_id`` starts as
        the empty Variant: following its wire upstream through Set and Delete
        Variant Attribute, structure tunnels, and a loop's shift registers (the
        value wired in and the value fed back), every source is an empty Variant
        constant or an unwired input. Anything else (a control, a subVI's
        output, another node's output, a constant holding a value) is not empty.
        """
        return self._empty_upstream(terminal_id, set())

    def _empty_upstream(self, terminal_id: str, seen: set[str]) -> bool:
        """Whether every wire into ``terminal_id`` starts empty; ``seen`` holds
        the terminals being followed, so a shift register's feedback loop adds no
        source of its own."""
        if terminal_id in seen:
            return True
        seen.add(terminal_id)
        home = self.terminal_vi(terminal_id)
        sources = [
            src
            for src in self.incoming_edges(terminal_id)
            if self.terminal_vi(src.terminal_id) == home
        ]
        sources += self._fed_back(terminal_id)
        return all(self._source_is_empty(src, seen) for src in sources)

    def _source_is_empty(self, src: WireEnd, seen: set[str]) -> bool:
        node = self.get_graph_node(src.node_id)
        if isinstance(node, ConstantNode):
            return node.empty_variant
        if isinstance(node, PrimitiveNode):
            term = self.get_terminal(src.terminal_id)
            if (
                node.prim_id in _ATTRIBUTE_EDITORS
                and term is not None
                and term.index == _VARIANT_OUT
            ):
                variant_in = next(
                    (
                        t
                        for t in node.terminals
                        if t.index == _VARIANT_IN and t.direction == "input"
                    ),
                    None,
                )
                return variant_in is None or self._empty_upstream(variant_in.id, seen)
            return False
        if isinstance(node, StructureNode) and isinstance(
            self.get_terminal(src.terminal_id), TunnelTerminal
        ):
            return self._empty_upstream(src.terminal_id, seen)
        return False

    def _fed_back(self, terminal_id: str) -> list[WireEnd]:
        """For the inside of a loop's left shift register, the wire into the
        paired right shift register's inside (the value for the next iteration);
        none for any other terminal. The registers pair by position, left with
        right."""
        term = self.get_terminal(terminal_id)
        if (
            not isinstance(term, TunnelTerminal)
            or term.tunnel_type != "lSR"
            or term.boundary != "inner"
        ):
            return []
        node = self.get_graph_node(self.terminal_node_id(terminal_id))
        if node is None:
            return []
        tunnels = [t for t in node.terminals if isinstance(t, TunnelTerminal)]
        lefts = [t for t in tunnels if t.tunnel_type == "lSR" and t.boundary == "inner"]
        rights = [
            t for t in tunnels if t.tunnel_type == "rSR" and t.boundary == "inner"
        ]
        right = next(
            (r for lt, r in pair_by_position(lefts, rights) if lt.id == terminal_id),
            None,
        )
        if right is None:
            return []
        home = self.terminal_vi(right.id)
        return [
            src
            for src in self.incoming_edges(right.id)
            if self.terminal_vi(src.terminal_id) == home
        ]

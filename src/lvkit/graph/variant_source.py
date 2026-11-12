"""Where a variant wire starts: whether it is a lookup table.

LabVIEW's "variant as lookup table" (a variant read or given attributes) is a
Python ``dict`` of the attributes, decided at generation time by this query. A
variant is a lookup table when it starts as the empty Variant or as a connector
pane Variant control of the VI being generated: a variant parameter read for
attributes is a dict the caller passes, and a generated caller builds one with
Set Variant Attribute.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import FPTerminal, Terminal, TunnelTerminal, VariantData
from .models import (
    AnyGraphNode,
    ConstantNode,
    PrimitiveNode,
    StructureNode,
    VINode,
    WireEnd,
)
from .shift_registers import pair_by_position

if TYPE_CHECKING:
    from ..vilib_resolver import VILibResolver

# A terminal followed upstream, with the ids of the subVI calls it is followed
# under (innermost last).
_Visit = tuple[str, tuple[str, ...]]

# The primitives whose variant output is the variant at their variant input
# (index 7) with attributes set or deleted, or unchanged: Set Variant Attribute,
# Get Variant Attribute and Delete Variant Attribute. Their variant output is
# index 3.
_ATTRIBUTE_EDITORS = frozenset({8204, 8205, 8206})
_VARIANT_IN = 7
_VARIANT_OUT = 3


class VariantSourceMixin:
    """The ``variant_lookup_parameters`` query on ``InMemoryVIGraph``."""

    if TYPE_CHECKING:

        def get_graph_node(self, node_id: str) -> AnyGraphNode | None: ...
        def get_terminal(self, terminal_id: str) -> Terminal | None: ...
        def incoming_edges(self, terminal_id: str) -> list[WireEnd]: ...
        def terminal_vi(self, terminal_id: str) -> str | None: ...
        def terminal_node_id(self, terminal_id: str) -> str: ...
        def loaded_callee_key(self, node: VINode) -> str | None: ...
        @property
        def vilib_catalog(self) -> VILibResolver: ...

    def variant_lookup_parameters(self, terminal_id: str) -> list[str] | None:
        """The connector-pane Variant controls the variant that reaches the input
        ``terminal_id`` is a lookup table through, or None when it is not a lookup
        table. An empty list is a table that starts empty.

        Following its wire upstream through Set and Delete Variant Attribute,
        structure tunnels, a loop's shift registers (the value wired in and the
        value fed back) and into the loaded subVIs that produce it, every source
        is an empty Variant constant (with or without attributes, the table
        starting with them), an unwired input or a connector-pane
        control of the VI the wire is on (a parameter: the dict its caller
        passes). Anything else (a control that is not on the connector pane, a
        subVI that is not loaded or that the catalog implements, another node's
        output, a constant holding a value) is not a lookup table.

        Into a subVI: a subVI call's output continues at the diagram wire into
        the called VI's indicator; a control of the called VI continues at the
        wire into the matching input of the call being followed. The calls
        followed so far are kept, so a VI called from several places is traced
        from the right caller; a call met again while it is being followed (a
        recursive VI) is not a lookup table.
        """
        parameters: set[str] = set()
        if not self._empty_upstream(terminal_id, set(), (), parameters):
            return None
        return sorted(parameters)

    def _empty_upstream(
        self,
        terminal_id: str,
        seen: set[_Visit],
        calls: tuple[VINode, ...],
        parameters: set[str],
    ) -> bool:
        """Whether every wire into ``terminal_id`` starts empty or at a parameter
        (added to ``parameters``); ``seen`` holds the terminals being followed
        (with the calls they are followed under), so a shift register's feedback
        loop adds no source of its own."""
        visit = (terminal_id, tuple(c.id for c in calls))
        if visit in seen:
            return True
        seen.add(visit)
        return all(
            self._source_is_empty(src, seen, calls, parameters)
            for src in self._own_sources(terminal_id)
        )

    def _own_sources(self, terminal_id: str) -> list[WireEnd]:
        """The sources of the wire into ``terminal_id`` on its own VI's diagram,
        and for a loop's left shift register the value fed back."""
        return [*self._diagram_sources(terminal_id), *self._fed_back(terminal_id)]

    def _diagram_sources(self, terminal_id: str) -> list[WireEnd]:
        home = self.terminal_vi(terminal_id)
        return [
            src
            for src in self.incoming_edges(terminal_id)
            if self.terminal_vi(src.terminal_id) == home
        ]

    def _source_is_empty(
        self,
        src: WireEnd,
        seen: set[_Visit],
        calls: tuple[VINode, ...],
        parameters: set[str],
    ) -> bool:
        node = self.get_graph_node(src.node_id)
        if isinstance(node, ConstantNode):
            return node.empty_variant or (
                isinstance(node.structured_value, VariantData)
                and node.structured_value.value_type is None
            )
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
                return variant_in is None or self._empty_upstream(
                    variant_in.id, seen, calls, parameters
                )
            return False
        if isinstance(node, StructureNode) and isinstance(
            self.get_terminal(src.terminal_id), TunnelTerminal
        ):
            return self._empty_upstream(src.terminal_id, seen, calls, parameters)
        if isinstance(node, VINode):
            return self._vi_source_is_empty(node, src, seen, calls, parameters)
        return False

    def _vi_source_is_empty(
        self,
        node: VINode,
        src: WireEnd,
        seen: set[_Visit],
        calls: tuple[VINode, ...],
        parameters: set[str],
    ) -> bool:
        """A wire that starts at a VI node: a control of the VI being followed
        (continue at the call that supplies it; with no call being followed, a
        connector-pane control is a parameter) or the output of a subVI call
        (continue inside the called VI)."""
        term = self.get_terminal(src.terminal_id)
        if term is None:
            return False
        if node.id == node.vi_path:
            if term.direction != "input":
                return False
            if not calls:
                if isinstance(term, FPTerminal) and term.is_public:
                    parameters.add(term.id)
                    return True
                return False
            call = calls[-1]
            supplied = [
                e.terminal_id
                for e in self.incoming_edges(term.id)
                if e.node_id == call.id
            ]
            return bool(supplied) and all(
                self._empty_upstream(t, seen, calls[:-1], parameters) for t in supplied
            )
        if term.direction != "output" or any(c.id == node.id for c in calls):
            return False
        key = self.loaded_callee_key(node)
        if key is None or self.vilib_catalog.resolve_recorded(node.qualified_path):
            return False
        indicators = [
            e.terminal_id
            for e in self.incoming_edges(term.id)
            if self.terminal_vi(e.terminal_id) == key
        ]
        inside = (*calls, node)
        return bool(indicators) and all(
            self._diagram_sources(t)
            and self._empty_upstream(t, seen, inside, parameters)
            for t in indicators
        )

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
        return self._diagram_sources(right.id)

"""Operations mixin for InMemoryVIGraph.

Methods: top_level_nodes, child_nodes, enriched_terminals,
_tunnels_from_terminals, _enrich_subvi_terminals_typed, _get_slot_to_name,
_sort_inner_uids, _get_children_of. Also exports the module-level
``frame_key`` helper.

These are the graph-native tree-walk ergonomics a generator uses to walk
``top_level_nodes`` -> ``child_nodes`` and read ``enriched_terminals``,
consuming ``GraphNode``s directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from ..models import (
    CaseFrame,
    EventFrame,
    FPTerminal,
    Frame,
    SequenceFrame,
    Terminal,
    Tunnel,
    TunnelTerminal,
)
from .core import (
    _OPERATION_KINDS,
    _graph_node_to_op_kind,
    _node_order_key,
)
from .models import (
    AnyGraphNode,
    PolyInfo,
    VINode,
)


class OperationsMixin:
    """Mixin providing graph-native node-tree walk methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _poly_info: dict[str, PolyInfo]

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def resolve_vi_name(self, vi_name: str) -> str: ...
        def get_poly_variants(self, vi_name: str) -> list[str]: ...
        def get_operation_order(
            self, vi_name: str, extra_kinds: tuple[str, ...] = ()
        ) -> list[str]: ...

    # ------------------------------------------------------------------ #
    # Graph-native ergonomic helpers -- single-sourced from the graph. A
    # generator walks ``top_level_nodes`` -> ``child_nodes`` and reads
    # ``enriched_terminals``, consuming ``GraphNode``s directly.
    # ------------------------------------------------------------------ #
    def top_level_nodes(
        self, vi_name: str, extra_kinds: tuple[str, ...] = ()
    ) -> list[AnyGraphNode]:
        """Top-level graph nodes of a VI in dataflow execution order.

        Yields the ``GraphNode``s themselves (single source of truth). Walk a
        structure's contents with :meth:`child_nodes`; get a subVI's
        callee-resolved terminals with :meth:`enriched_terminals`.

        The order (and the ``_OPERATION_KINDS`` gate, widened by
        ``extra_kinds``) is single-sourced in ``get_operation_order`` -- this
        just maps its ordered uids straight to their ``GraphNode``s, with no
        separate scan or unreachable fallback (``get_operation_order`` already
        includes every top-level node the old manual scan did, via the
        identical ``op_kind in allowed_kinds and parent is None`` filter).
        """
        vi_name = self.resolve_vi_name(vi_name)
        nodes: list[AnyGraphNode] = []
        for uid in self.get_operation_order(vi_name, extra_kinds=extra_kinds):
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is not None:
                nodes.append(gnode)
        return nodes

    def child_nodes(
        self,
        parent_uid: str,
        vi_name: str,
        extra_kinds: tuple[str, ...] = (),
    ) -> list[AnyGraphNode]:
        """Graph nodes directly contained in a structure whose kind is in
        ``_OPERATION_KINDS`` (widened by ``extra_kinds``, mirroring
        ``get_operation_order``'s own parameter -- e.g. netlist widening to
        also keep a top-level local-variable node), in deterministic order --
        the tree step over ``GraphNode.children`` (via ``_get_children_of``).
        """
        allowed_kinds = (
            _OPERATION_KINDS if not extra_kinds else (*_OPERATION_KINDS, *extra_kinds)
        )
        out: list[AnyGraphNode] = []
        uids = self._sort_inner_uids(
            self._get_children_of(parent_uid, vi_name), vi_name
        )
        for uid in uids:
            if uid not in self._graph:
                continue
            g = self._graph.nodes[uid].get("node")
            if g is not None and _graph_node_to_op_kind(g) in allowed_kinds:
                out.append(g)
        return out

    def enriched_terminals(self, node: AnyGraphNode, vi_name: str) -> list[Terminal]:
        """A node's terminals with subVI-call terminals enriched with the
        callee's parameter names (resolved by the callee's QUALIFIED name).
        """
        terminals = list(node.terminals)
        if isinstance(node, VINode) and node.id != node.vi_path:
            terminals = self._enrich_subvi_terminals_typed(
                terminals, node.qualified_name or node.name, vi_name
            )
        return terminals

    @staticmethod
    def _tunnels_from_terminals(terminals: list[Terminal]) -> list[Tunnel]:
        """Reconstruct Tunnel objects from StructureNode's terminal metadata.

        Iterates BOTH outer and inner TunnelTerminals to build the full
        tunnel set. This is necessary because each outer terminal's
        paired_id stores only ONE inner (the first frame's), but case
        structures have one inner per frame. Inner terminals' paired_id
        points back to the outer, so iterating them captures all pairs.
        """
        tunnels: list[Tunnel] = []
        seen_pairs: set[tuple[str, str]] = set()

        for term in terminals:
            if not isinstance(term, TunnelTerminal):
                continue
            if not term.tunnel_type or not term.paired_id:
                continue

            if term.boundary == "outer":
                outer_uid = term.id
                inner_uid = term.paired_id
            elif term.boundary == "inner":
                outer_uid = term.paired_id
                inner_uid = term.id
            else:
                continue

            pair_key = (outer_uid, inner_uid)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            tunnels.append(
                Tunnel(
                    outer_terminal_uid=outer_uid,
                    inner_terminal_uid=inner_uid,
                    tunnel_type=term.tunnel_type,
                    # term is either boundary side of THIS tunnel -- construction.py
                    # stamps the same mode/conditional/sr_initialized/sr_stack_depth
                    # on both the outer and inner TunnelTerminal, so either one
                    # carries the value faithfully.
                    mode=term.mode,
                    conditional=term.conditional,
                    sr_initialized=term.sr_initialized,
                    sr_stack_depth=term.sr_stack_depth,
                )
            )

        return tunnels

    def _enrich_subvi_terminals_typed(
        self,
        terminals: list[Terminal],
        subvi_name: str | None,
        caller_vi: str,
    ) -> list[Terminal]:
        """Add callee parameter names to SubVI terminals via _get_slot_to_name.

        ``subvi_name`` should be the callee's QUALIFIED name/vi_key when
        available (not a bare filename) -- resolve_vi_name's bare-name lookup
        collides across same-named VIs (e.g. two classes' same-named
        dynamic-dispatch override, both literally "run.vi")."""
        if not subvi_name:
            return terminals
        resolved_name = self.resolve_vi_name(subvi_name)
        if resolved_name not in self._vi_nodes:
            return terminals

        # Get callee slot -> name mapping
        slot_to_name = self._get_slot_to_name(resolved_name)

        # Polymorphic VIs may have no FP terminals on the wrapper
        if not slot_to_name:
            for variant in self.get_poly_variants(resolved_name):
                resolved_variant = self.resolve_vi_name(variant)
                if resolved_variant in self._vi_nodes:
                    slot_to_name = self._get_slot_to_name(resolved_variant)
                    if slot_to_name:
                        break

        # Enrich terminals -- set name from callee FP if available
        enriched: list[Terminal] = []
        for t in terminals:
            name = t.name
            if t.index is not None and t.index in slot_to_name:
                name = slot_to_name[t.index]
            if isinstance(t, FPTerminal):
                enriched.append(
                    FPTerminal(
                        id=t.id,
                        index=t.index,
                        direction=t.direction,
                        name=name,
                        lv_type=t.lv_type,
                        wiring_rule=t.wiring_rule,
                        is_indicator=t.is_indicator,
                        is_public=t.is_public,
                        control_type=t.control_type,
                        default_value=t.default_value,
                        enum_values=t.enum_values,
                    )
                )
            else:
                enriched.append(
                    Terminal(
                        id=t.id,
                        index=t.index,
                        direction=t.direction,
                        name=name,
                        lv_type=t.lv_type,
                    )
                )
        return enriched

    def _get_slot_to_name(self, vi_name: str) -> dict[int, str]:
        """Get index -> terminal name mapping for a VI's FP terminals."""
        if vi_name not in self._graph:
            return {}
        gnode = self._graph.nodes[vi_name].get("node")
        if not isinstance(gnode, VINode):
            return {}
        result: dict[int, str] = {}
        for t in gnode.terminals:
            if t.index is not None and t.name:
                result[t.index] = t.name
        return result

    def _sort_inner_uids(
        self,
        uids: list[str],
        vi_name: str,
    ) -> list[str]:
        """Topologically sort inner node UIDs by their wire dependencies.

        Only considers real operation nodes to avoid false dependency
        cycles from tunnel terminal wiring.
        """
        uid_set = set(uids)
        if len(uid_set) <= 1:
            return list(uids)

        # Filter to real operations only
        op_uid_set: set[str] = set()
        for uid in uid_set:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            op_kind = _graph_node_to_op_kind(gnode)
            if op_kind in _OPERATION_KINDS:
                op_uid_set.add(uid)

        if len(op_uid_set) <= 1:
            return list(uids)

        # Build dependency graph among inner operation nodes. Insert nodes in
        # a deterministic order (op_uid_set is hash-randomized) and break
        # topological ties with the same key, so parallel inner ops emit in a
        # stable order — codegen must be reproducible.
        dep = nx.DiGraph()
        dep.add_nodes_from(sorted(op_uid_set, key=_node_order_key))

        for uid in sorted(op_uid_set, key=_node_order_key):
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if dest in op_uid_set and dest != uid:
                    dep.add_edge(uid, dest)

        try:
            sorted_ops = list(
                nx.lexicographical_topological_sort(dep, key=_node_order_key)
            )
        except nx.NetworkXUnfeasible:
            sorted_ops = sorted(op_uid_set, key=_node_order_key)

        # Build final list: sorted ops first, then non-op uids
        sorted_set = set(sorted_ops)
        result = list(sorted_ops)
        for uid in uids:
            if uid not in sorted_set:
                result.append(uid)

        return result

    def _get_children_of(
        self,
        parent_uid: str,
        vi_name: str,
    ) -> list[str]:
        """UIDs of the nodes whose parent == parent_uid, in deterministic
        ``_node_order_key`` order.

        Reads the FORWARD containment adjacency stamped at construction
        (``GraphNode.children``) — an O(1) lookup instead of the whole-VI
        reverse scan this used to do. The stored list is already sorted by
        ``_node_order_key``, so the result is byte-identical to the old scan.
        Falls back to the scan only when ``parent_uid`` is not a graph node
        (shouldn't happen for a real structure)."""
        owner = self._graph.nodes.get(parent_uid, {}).get("node")
        if owner is not None:
            return owner.children
        node_uids = self._vi_nodes.get(vi_name, set())
        children: list[str] = []
        for uid in node_uids:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is not None and gnode.parent == parent_uid:
                children.append(uid)
        return sorted(children, key=_node_order_key)


def frame_key(frame: Frame, position: int) -> str | int | None:
    """The match key a structure's children are grouped by, for pairing a
    given ``Frame`` object with the ``GraphNode``s that live inside it: a
    case/disable frame's raw ``selector_value``, a sequence frame's
    ``str(index)``, an event frame's ``str(position)`` (its LIST POSITION,
    not its own ``.index`` -- an event frame carries no selector of its own,
    so ``construction.py``'s ``_stamp`` keys its children by diagram-order
    position instead). ``None`` for any other ``Frame`` subtype (there are
    currently only these three -- see ``models.Frame``).

    Single-sourced here so ``codegen.context.CodeGenContext.frame_children``,
    ``graph.describe``'s frame/child matching, ``graph.diff``'s element
    correlation, and the netlist builder's frame-child lookup all group a
    structure's children the SAME way -- this used to be reimplemented (with
    subtly different fallback ordering) in all four places.
    """
    if isinstance(frame, CaseFrame):
        return frame.selector_value
    if isinstance(frame, SequenceFrame):
        return str(frame.index)
    if isinstance(frame, EventFrame):
        return str(position)
    return None

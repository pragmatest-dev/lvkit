"""Operations mixin for InMemoryVIGraph.

Methods: _build_operation, _tunnels_from_terminals, _enrich_subvi_terminals_typed,
_get_slot_to_name, resolve_name, _sort_inner_uids, _build_inner_nodes,
_get_children_of, _build_frames_from_parent, _build_sequence_frames_from_parent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from ..graph_types import (
    CaseFrame,
    CaseOperation,
    CaseStructureNode,
    FPTerminal,
    InvokeOperation,
    LoopNode,
    LoopOperation,
    Operation,
    PolyInfo,
    PrimitiveOperation,
    PropertyOperation,
    SequenceFrame,
    SequenceNode,
    SequenceOperation,
    StructureNode,
    SubVIOperation,
    Terminal,
    Tunnel,
    TunnelTerminal,
    VINode,
)
from ..graph_types import (
    PrimitiveNode as GraphPrimitiveNode,
)
from .core import (
    _NODE_TYPE_NAMES,
    _OPERATION_KINDS,
    _get_operation_labels,
    _graph_node_to_op_kind,
)


class OperationsMixin:
    """Mixin providing operation building and inner-node methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _poly_info: dict[str, PolyInfo]

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def resolve_vi_name(self, vi_name: str) -> str: ...
        def get_poly_variants(self, vi_name: str) -> list[str]: ...

    def _build_operation(
        self, uid: str, vi_name: str,
    ) -> Operation:
        """Build a single Operation dataclass from a typed graph node.

        This is the ONE place that constructs Operation objects.
        """
        gnode = self._graph.nodes[uid].get("node")
        if gnode is None:
            return Operation(id=uid, name=None, labels=["Operation"])

        op_kind = _graph_node_to_op_kind(gnode)
        labels = _get_operation_labels(op_kind)

        # Build terminals, enriching SubVI terminals with callee param names
        terminals = list(gnode.terminals)
        if isinstance(gnode, VINode) and gnode.id != gnode.vi:
            # SubVI call — enrich with callee param names via resolve_name
            terminals = self._enrich_subvi_terminals_typed(
                terminals, gnode.name, vi_name
            )

        # Structure-specific fields
        tunnels: list[Tunnel] = []
        inner_nodes: list[Operation] = []
        loop_type: str | None = None
        stop_cond: str | None = None
        case_frames: list[CaseFrame] = []
        seq_frames: list[SequenceFrame] = []
        selector_terminal: str | None = None
        node_type = gnode.node_type or ""

        if isinstance(gnode, StructureNode):
            # Reconstruct Tunnel objects from terminal metadata
            tunnels = self._tunnels_from_terminals(gnode.terminals)

            # Query inner nodes by parent
            child_uids = self._get_children_of(uid, vi_name)

            if isinstance(gnode, LoopNode):
                labels = ["Loop"]
                loop_type = gnode.loop_type
                inner_nodes = self._build_inner_nodes(
                    child_uids, vi_name,
                )
                stop_cond = gnode.stop_condition_terminal

            elif isinstance(gnode, CaseStructureNode):
                labels = ["CaseStructure"]
                selector_terminal = gnode.selector_terminal
                case_frames = self._populate_frame_operations(  # type: ignore[assignment]
                    gnode.frames, vi_name, child_uids,
                )

            elif isinstance(gnode, SequenceNode):
                labels = ["FlatSequence"]
                seq_frames = self._populate_frame_operations(  # type: ignore[assignment]
                    gnode.frames, vi_name, child_uids,
                )

        # Name fallback for unnamed structures
        node_name = gnode.name
        if not node_name and node_type:
            node_name = _NODE_TYPE_NAMES.get(node_type)

        # Common kwargs for all operation types
        common = {
            "id": uid,
            "name": node_name,
            "labels": labels,
            "terminals": terminals,
            "node_type": node_type or None,
            "tunnels": tunnels,
            "inner_nodes": inner_nodes,
            "description": gnode.description,
            "qualified_path": getattr(gnode, "qualified_path", None),
        }

        # Build the right operation subtype
        if isinstance(gnode, CaseStructureNode):
            return CaseOperation(
                **common,
                frames=case_frames,
                selector_terminal=selector_terminal,
            )
        if isinstance(gnode, SequenceNode):
            return SequenceOperation(
                **common,
                frames=seq_frames,
            )
        if isinstance(gnode, LoopNode):
            return LoopOperation(
                **common,
                loop_type=loop_type,
                stop_condition_terminal=stop_cond,
            )
        if isinstance(gnode, VINode):
            return SubVIOperation(
                **common,
                poly_variant_name=gnode.poly_variant_name,
            )
        if isinstance(gnode, GraphPrimitiveNode):
            if gnode.properties:
                return PropertyOperation(
                    **common,
                    object_name=gnode.object_name,
                    object_method_id=gnode.object_method_id,
                    properties=list(gnode.properties),
                )
            if gnode.method_name:
                return InvokeOperation(
                    **common,
                    object_name=gnode.object_name,
                    object_method_id=gnode.object_method_id,
                    method_name=gnode.method_name,
                    method_code=gnode.method_code,
                )
            return PrimitiveOperation(
                **common,
                primResID=gnode.prim_id,
                operation=gnode.operation,
            )

        # Fallback: base Operation
        return Operation(**common)

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

            tunnels.append(Tunnel(
                outer_terminal_uid=outer_uid,
                inner_terminal_uid=inner_uid,
                tunnel_type=term.tunnel_type,
            ))

        return tunnels

    def _enrich_subvi_terminals_typed(
        self,
        terminals: list[Terminal],
        subvi_name: str | None,
        caller_vi: str,
    ) -> list[Terminal]:
        """Add callee parameter names to SubVI terminals via resolve_name."""
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
                enriched.append(FPTerminal(
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
                ))
            else:
                enriched.append(Terminal(
                    id=t.id,
                    index=t.index,
                    direction=t.direction,
                    name=name,
                    lv_type=t.lv_type,
                ))
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

    def resolve_name(self, node_id: str, terminal_index: int) -> str | None:
        """Resolve the name of a terminal on a node.

        For SubVI calls, follows the graph to the callee VI and reads
        the terminal name from its FP terminal list.
        """
        if node_id not in self._graph:
            return None
        gnode = self._graph.nodes[node_id].get("node")
        if gnode is None:
            return None

        # Direct: read from node's terminal list
        for term in gnode.terminals:
            if term.index == terminal_index and term.name:
                return term.name

        # SubVI: follow graph to callee VI, read its terminal name
        if isinstance(gnode, VINode) and gnode.id != gnode.vi:
            callee_name = self.resolve_vi_name(gnode.name or "")
            if callee_name in self._graph:
                callee_node = self._graph.nodes[callee_name].get("node")
                if isinstance(callee_node, VINode):
                    for term in callee_node.terminals:
                        if term.index == terminal_index and term.name:
                            return term.name

        return None


    def _sort_inner_uids(
        self, uids: list[str], vi_name: str,
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

        # Build dependency graph among inner operation nodes
        dep = nx.DiGraph()
        dep.add_nodes_from(op_uid_set)

        for uid in op_uid_set:
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if dest in op_uid_set and dest != uid:
                    dep.add_edge(uid, dest)

        try:
            sorted_ops = list(nx.topological_sort(dep))
        except nx.NetworkXUnfeasible:
            sorted_ops = list(op_uid_set)

        # Build final list: sorted ops first, then non-op uids
        sorted_set = set(sorted_ops)
        result = list(sorted_ops)
        for uid in uids:
            if uid not in sorted_set:
                result.append(uid)

        return result

    def _build_inner_nodes(
        self, uids: list[str], vi_name: str,
    ) -> list[Operation]:
        """Build Operation dataclasses for nodes inside a structure."""
        sorted_uids = self._sort_inner_uids(uids, vi_name)
        results = []
        for uid in sorted_uids:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            op_kind = _graph_node_to_op_kind(gnode)
            if op_kind in _OPERATION_KINDS:
                results.append(self._build_operation(uid, vi_name))
        return results

    def _get_children_of(
        self, parent_uid: str, vi_name: str,
    ) -> list[str]:
        """Get UIDs of all graph nodes whose parent == parent_uid."""
        node_uids = self._vi_nodes.get(vi_name, set())
        children: list[str] = []
        for uid in node_uids:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is not None and gnode.parent == parent_uid:
                children.append(uid)
        return children

    def _populate_frame_operations(
        self,
        frames: list[CaseFrame] | list[SequenceFrame],
        vi_name: str,
        child_uids: list[str],
    ) -> list[CaseFrame] | list[SequenceFrame]:  # type: ignore[return]
        """Populate operations on existing frames from graph children."""
        frame_to_uids = self._group_children_by_frame(child_uids)

        for frame in frames:
            # Match by selector_value (cases) or index (sequences)
            if isinstance(frame, CaseFrame):
                key = frame.selector_value
            elif isinstance(frame, SequenceFrame):
                key = str(frame.index)
            else:
                continue
            uids = frame_to_uids.get(key, [])
            frame.inner_node_uids = uids
            frame.operations = self._build_inner_nodes(uids, vi_name)

        return frames

    def _group_children_by_frame(
        self, child_uids: list[str],
    ) -> dict[str | int | None, list[str]]:
        """Group child UIDs by their frame attribute."""
        frame_to_uids: dict[str | int | None, list[str]] = {}
        for uid in child_uids:
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            fv = gnode.frame
            if fv not in frame_to_uids:
                frame_to_uids[fv] = []
            frame_to_uids[fv].append(uid)
        return frame_to_uids

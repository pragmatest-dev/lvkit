"""Operations mixin for InMemoryVIGraph.

Methods: _build_operation, _tunnels_from_terminals, _enrich_subvi_terminals_typed,
_get_slot_to_name, resolve_name, _sort_inner_uids, _build_inner_nodes,
_get_children_of, _build_case_frames_from_parent, _build_sequence_frames_from_parent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import networkx as nx

from ..graph_types import (
    CaseFrame,
    FPTerminal,
    FrameInfo,
    Operation,
    PolyInfo,
    PropertyDef,
    StructureNode,
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

if TYPE_CHECKING:
    pass


class OperationsMixin:
    """Mixin providing operation building and inner-node methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _poly_info: dict[str, PolyInfo]

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
        selector_terminal: str | None = None
        node_type = gnode.node_type or ""

        if isinstance(gnode, StructureNode):
            # Reconstruct Tunnel objects from terminal metadata
            tunnels = self._tunnels_from_terminals(gnode.terminals)

            # Query inner nodes by parent
            child_uids = self._get_children_of(uid, vi_name)

            if node_type in ("whileLoop", "forLoop"):
                labels = ["Loop"]
                loop_type = node_type
                inner_nodes = self._build_inner_nodes(
                    child_uids, vi_name
                )
                stop_cond = gnode.stop_condition_terminal

            elif node_type in ("caseStruct", "select"):
                labels = ["CaseStructure"]
                selector_terminal = gnode.selector_terminal
                case_frames = self._build_case_frames_from_parent(
                    uid, gnode.frames, vi_name, child_uids
                )

            elif node_type in ("flatSequence", "seq"):
                labels = ["FlatSequence"]
                case_frames = self._build_sequence_frames_from_parent(
                    uid, gnode.frames, vi_name, child_uids
                )

        # Name fallback for unnamed structures
        node_name = gnode.name
        if not node_name and node_type:
            node_name = _NODE_TYPE_NAMES.get(node_type)

        # Extract primitive-specific fields
        prim_id: int | None = None
        operation: str | None = None
        object_name: str | None = None
        object_method_id: str | None = None
        properties: list[PropertyDef] = []
        method_name: str | None = None
        method_code: int | None = None
        poly_variant_name: str | None = None

        if isinstance(gnode, GraphPrimitiveNode):
            prim_id = gnode.prim_id
            operation = gnode.operation
            object_name = gnode.object_name
            object_method_id = gnode.object_method_id
            properties = list(gnode.properties)
            method_name = gnode.method_name
            method_code = gnode.method_code

        if isinstance(gnode, VINode):
            poly_variant_name = gnode.poly_variant_name

        return Operation(
            id=uid,
            name=node_name,
            labels=labels,
            primResID=prim_id,
            terminals=terminals,
            node_type=node_type or None,
            loop_type=loop_type,
            tunnels=tunnels,
            inner_nodes=inner_nodes,
            stop_condition_terminal=stop_cond,
            description=gnode.description,
            operation=operation,
            object_name=object_name,
            object_method_id=object_method_id,
            properties=properties,
            method_name=method_name,
            method_code=method_code,
            case_frames=case_frames,
            selector_terminal=selector_terminal,
            poly_variant_name=poly_variant_name,
        )

    @staticmethod
    def _tunnels_from_terminals(terminals: list[Terminal]) -> list[Tunnel]:
        """Reconstruct Tunnel objects from StructureNode's terminal metadata.

        Pairs outer/inner terminals by paired_id to rebuild the Tunnel
        objects that codegen consumers expect on Operation.tunnels.
        """
        tunnels: list[Tunnel] = []
        seen_pairs: set[tuple[str, str]] = set()

        for term in terminals:
            if not isinstance(term, TunnelTerminal):
                continue
            if not term.tunnel_type or not term.paired_id:
                continue
            if term.boundary != "outer":
                continue

            outer_uid = term.id
            inner_uid = term.paired_id
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

    def _build_case_frames_from_parent(
        self,
        structure_uid: str,
        frame_infos: list[FrameInfo],
        vi_name: str,
        child_uids: list[str],
    ) -> list[CaseFrame]:
        """Build CaseFrame dataclasses by grouping children by frame."""
        # Group child UIDs by their frame value
        frame_to_uids: dict[str | int | None, list[str]] = {}
        for uid in child_uids:
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            fv = gnode.frame
            if fv not in frame_to_uids:
                frame_to_uids[fv] = []
            frame_to_uids[fv].append(uid)

        result_frames: list[CaseFrame] = []
        for fi in frame_infos:
            uids = frame_to_uids.get(fi.selector_value, [])
            frame_ops = self._build_inner_nodes(uids, vi_name)
            result_frames.append(CaseFrame(
                selector_value=fi.selector_value,
                inner_node_uids=uids,
                operations=frame_ops,
                is_default=fi.is_default,
            ))

        return result_frames

    def _build_sequence_frames_from_parent(
        self,
        structure_uid: str,
        frame_infos: list[FrameInfo],
        vi_name: str,
        child_uids: list[str],
    ) -> list[CaseFrame]:
        """Build CaseFrame dataclasses from sequence frames by parent query."""
        # Group child UIDs by their frame value
        frame_to_uids: dict[str | int | None, list[str]] = {}
        for uid in child_uids:
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            fv = gnode.frame
            if fv not in frame_to_uids:
                frame_to_uids[fv] = []
            frame_to_uids[fv].append(uid)

        result_frames: list[CaseFrame] = []
        for fi in frame_infos:
            uids = frame_to_uids.get(fi.selector_value, [])
            frame_ops = self._build_inner_nodes(uids, vi_name)
            result_frames.append(CaseFrame(
                selector_value=fi.selector_value,
                inner_node_uids=uids,
                operations=frame_ops,
            ))

        return result_frames

"""Operations mixin for InMemoryVIGraph.

Methods: _build_operation, _tunnels_from_terminals, _enrich_subvi_terminals_typed,
_get_slot_to_name, _sort_inner_uids, _build_inner_nodes,
_get_children_of, _build_frames_from_parent, _build_sequence_frames_from_parent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import networkx as nx

from ..models import (
    CaseFrame,
    CaseOperation,
    DisableStructureOperation,
    EventFrame,
    EventOperation,
    FeedbackOperation,
    FormulaOperation,
    FPTerminal,
    InPlaceOperation,
    InvokeOperation,
    LoopOperation,
    Operation,
    PrimitiveOperation,
    PropertyOperation,
    SequenceFrame,
    SequenceOperation,
    SubVIOperation,
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
    CaseStructureNode,
    DisableStructureNode,
    EventStructureNode,
    InPlaceNode,
    LoopNode,
    PolyInfo,
    SequenceNode,
    StructureNode,
    VINode,
)
from .models import (
    FormulaNode as GraphFormulaNode,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
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
        def get_operation_order(self, vi_name: str) -> list[str]: ...

    def _build_operation(
        self,
        uid: str,
        vi_name: str,
    ) -> Operation:
        """Build a single Operation dataclass from a typed graph node.

        This is the ONE place that constructs Operation objects.
        """
        gnode = self._graph.nodes[uid].get("node")
        if gnode is None:
            return Operation(id=uid, name=None, kind="operation")

        op_kind = _graph_node_to_op_kind(gnode)
        kind = op_kind

        # Build terminals, enriching SubVI terminals with callee param names
        terminals = list(gnode.terminals)
        if isinstance(gnode, VINode) and gnode.id != gnode.vi_path:
            # SubVI call — enrich with callee param names via
            # _enrich_subvi_terminals_typed. Resolve by the callee's
            # QUALIFIED name (e.g. "TestSuite.lvclass:run.vi"), never the bare
            # ``gnode.name`` ("run.vi") -- a bare-name lookup collides across
            # every same-named override in a dynamic-dispatch class hierarchy
            # (every class's override of a method is literally "run.vi").
            terminals = self._enrich_subvi_terminals_typed(
                terminals, gnode.qualified_name or gnode.name, vi_name
            )

        # Structure-specific fields
        tunnels: list[Tunnel] = []
        inner_nodes: list[Operation] = []
        loop_type: str | None = None
        stop_cond: str | None = None
        stop_cond_inverted = False
        parallel = False
        parallel_static_workers: int | None = None
        case_frames: list[CaseFrame] = []
        seq_frames: list[SequenceFrame] = []
        event_frames: list[EventFrame] = []
        selector_terminal: str | None = None
        decompose: list[PrimitiveOperation] = []
        recompose: list[PrimitiveOperation] = []
        node_type = gnode.node_type or ""

        if isinstance(gnode, StructureNode):
            # Reconstruct Tunnel objects from terminal metadata
            tunnels = self._tunnels_from_terminals(gnode.terminals)

            # Query inner nodes by parent
            child_uids = self._get_children_of(uid, vi_name)

            # `kind` already equals _graph_node_to_op_kind(gnode) from above
            # for every structure type (that helper's isinstance/node_type
            # checks mirror these branches exactly) — this switch only
            # handles the frames= / tunnels= / inner_nodes= fields, not kind.
            if isinstance(gnode, LoopNode):
                loop_type = gnode.loop_type
                inner_nodes = self._build_inner_nodes(
                    child_uids,
                    vi_name,
                )
                stop_cond = gnode.stop_condition_terminal
                stop_cond_inverted = gnode.stop_condition_inverted
                parallel = gnode.parallel
                parallel_static_workers = gnode.parallel_static_workers

            elif isinstance(gnode, DisableStructureNode):
                case_frames = self._populate_frame_operations(  # type: ignore[assignment]
                    gnode.frames,
                    vi_name,
                    child_uids,
                )

            elif isinstance(gnode, CaseStructureNode):
                selector_terminal = gnode.selector_terminal
                case_frames = self._populate_frame_operations(  # type: ignore[assignment]
                    gnode.frames,
                    vi_name,
                    child_uids,
                )

            elif isinstance(gnode, SequenceNode):
                seq_frames = self._populate_frame_operations(  # type: ignore[assignment]
                    gnode.frames,
                    vi_name,
                    child_uids,
                )

            elif isinstance(gnode, InPlaceNode):
                all_inner = self._build_inner_nodes(child_uids, vi_name)
                decompose, recompose, inner_nodes = _classify_ipes_ops(all_inner)

            elif isinstance(gnode, EventStructureNode):
                event_frames = self._populate_frame_operations(  # type: ignore[assignment]
                    gnode.frames,
                    vi_name,
                    child_uids,
                )

        # Structures have no codegen identity; name stays None (see
        # Operation.display_name for how the type word is composed at the
        # point of use, not stored here).
        node_name = gnode.name

        # Common kwargs for all operation types
        common = {
            "id": uid,
            "name": node_name,
            "label": gnode.label,
            "caption": gnode.caption,
            "kind": kind,
            "terminals": terminals,
            "node_type": node_type or None,
            "tunnels": tunnels,
            "inner_nodes": inner_nodes,
            "description": gnode.description,
            "qualified_path": getattr(gnode, "qualified_path", None),
            "owning_libraries": list(getattr(gnode, "owning_libraries", []) or []),
            # Every named node carries its resolution identity; a node with no
            # library to qualify with falls back to its own name (never absent).
            "qualified_name": getattr(gnode, "qualified_name", None) or node_name,
        }

        # Build the right operation subtype
        if isinstance(gnode, InPlaceNode):
            return InPlaceOperation(
                **common,
                decompose_ops=decompose,
                recompose_ops=recompose,
            )
        if isinstance(gnode, DisableStructureNode):
            return DisableStructureOperation(
                **common,
                frames=case_frames,
                disable_kind=gnode.kind,
            )
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
                is_flat=gnode.is_flat,
            )
        if isinstance(gnode, EventStructureNode):
            return EventOperation(
                **common,
                frames=event_frames,
            )
        if isinstance(gnode, LoopNode):
            return LoopOperation(
                **common,
                loop_type=loop_type,
                stop_condition_terminal=stop_cond,
                stop_condition_inverted=stop_cond_inverted,
                parallel=parallel,
                parallel_static_workers=parallel_static_workers,
            )
        if isinstance(gnode, VINode):
            return SubVIOperation(
                **common,
                poly_variant_name=gnode.poly_variant_name,
            )
        if isinstance(gnode, GraphFormulaNode):
            return FormulaOperation(**common, script=gnode.script)
        if isinstance(gnode, GraphPrimitiveNode):
            # Feedback Node: the graph node is a GraphPrimitiveNode carrying
            # the master/slave link + delay as node attributes (see
            # construction.py). Lift them onto a FeedbackOperation so the
            # netlist can project it as a mu net and dissolve the write side.
            fb_is_master = self._graph.nodes[uid].get("feedback_is_master")
            if fb_is_master is not None:
                return FeedbackOperation(
                    **common,
                    is_master=fb_is_master,
                    partner_uid=self._graph.nodes[uid].get("feedback_partner"),
                    delay=self._graph.nodes[uid].get("feedback_delay"),
                )
            if gnode.properties:
                return PropertyOperation(
                    **common,
                    object_name=gnode.object_name,
                    object_method_id=gnode.object_method_id,
                    properties=list(gnode.properties),
                    value_terminal_ids=list(gnode.property_value_terminal_ids),
                )
            if gnode.method_name:
                return InvokeOperation(
                    **common,
                    object_name=gnode.object_name,
                    object_method_id=gnode.object_method_id,
                    method_name=gnode.method_name,
                    method_code=gnode.method_code,
                )
            poser_uid = self._graph.nodes[uid].get("poser_uid")
            return PrimitiveOperation(
                **common,
                primResID=gnode.prim_id,
                operation=gnode.operation,
                poser_uid=poser_uid,
            )

        # Fallback: base Operation
        return Operation(**common)

    # ------------------------------------------------------------------ #
    # Graph-native ergonomic helpers -- the tree-walk convenience that
    # ``get_operations``/``Operation`` provided, but single-sourced from the
    # graph (no projected ``Operation`` snapshot). A generator walks
    # ``top_level_nodes`` -> ``child_nodes`` and reads ``enriched_terminals``,
    # consuming ``GraphNode``s directly. See docs: the Operation-removal work.
    # ------------------------------------------------------------------ #
    def top_level_nodes(self, vi_name: str) -> list[AnyGraphNode]:
        """Top-level graph nodes of a VI in dataflow execution order.

        The SAME node selection + ordering ``get_operations`` uses, but yields
        the ``GraphNode``s themselves (single source of truth) instead of
        projected ``Operation``s. Walk a structure's contents with
        :meth:`child_nodes`; get a subVI's callee-resolved terminals with
        :meth:`enriched_terminals`.
        """
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []
        top: dict[str, AnyGraphNode] = {}
        for uid in node_uids:
            if uid == vi_name or uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            if (
                _graph_node_to_op_kind(gnode) in _OPERATION_KINDS
                and gnode.parent is None
            ):
                top[uid] = gnode
        ordered = [u for u in self.get_operation_order(vi_name) if u in top]
        seen = set(ordered)
        ordered += [u for u in sorted(top, key=_node_order_key) if u not in seen]
        return [top[u] for u in ordered]

    def child_nodes(self, parent_uid: str, vi_name: str) -> list[AnyGraphNode]:
        """Operation-kind graph nodes directly contained in a structure, in
        deterministic order -- the graph-native tree step
        (``GraphNode.children`` via ``_get_children_of``), matching
        ``_build_inner_nodes``' selection but returning ``GraphNode``s.
        """
        out: list[AnyGraphNode] = []
        uids = self._sort_inner_uids(
            self._get_children_of(parent_uid, vi_name), vi_name
        )
        for uid in uids:
            if uid not in self._graph:
                continue
            g = self._graph.nodes[uid].get("node")
            if g is not None and _graph_node_to_op_kind(g) in _OPERATION_KINDS:
                out.append(g)
        return out

    def enriched_terminals(
        self, node: AnyGraphNode, vi_name: str
    ) -> list[Terminal]:
        """A node's terminals with subVI-call terminals enriched with the
        callee's parameter names (resolved by the callee's QUALIFIED name) --
        the same enrichment ``get_operations`` bakes into
        ``Operation.terminals``, exposed for callers walking ``GraphNode``s.
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

    def _build_inner_nodes(
        self,
        uids: list[str],
        vi_name: str,
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

    def _populate_frame_operations(
        self,
        frames: list[CaseFrame] | list[SequenceFrame] | list[EventFrame],
        vi_name: str,
        child_uids: list[str],
    ) -> list[CaseFrame] | list[SequenceFrame] | list[EventFrame]:
        """Build a view of ``frames`` with operations populated.

        Returns FRESH frame copies for the Operation tree — the incoming
        ``frames`` (persistent state on the structure's graph node) are never
        mutated. A getter must not have side effects on the graph.
        """
        frame_to_uids = self._group_children_by_frame(child_uids)

        result: list[CaseFrame | SequenceFrame | EventFrame] = []
        for list_position, frame in enumerate(frames):
            # Match by selector_value (cases), index (sequences), or list
            # POSITION (events -- there's no runtime selector_value/index of
            # its own; construction.py stamps children with str(idx) in the
            # same diagram order the parser built es.frames in).
            if isinstance(frame, CaseFrame):
                key = frame.selector_value
            elif isinstance(frame, SequenceFrame):
                key = str(frame.index)
            elif isinstance(frame, EventFrame):
                key = str(list_position)
            else:
                result.append(frame)
                continue
            uids = frame_to_uids.get(key, [])
            result.append(
                frame.model_copy(
                    update={
                        "inner_node_uids": uids,
                        "operations": self._build_inner_nodes(uids, vi_name),
                    }
                )
            )

        return cast(
            "list[CaseFrame] | list[SequenceFrame] | list[EventFrame]",
            result,
        )

    def _group_children_by_frame(
        self,
        child_uids: list[str],
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


def _classify_ipes_ops(
    inner: list[Operation],
) -> tuple[list[PrimitiveOperation], list[PrimitiveOperation], list[Operation]]:
    """Split IPES inner ops into decompose, recompose, and regular.

    Decompose ops: PrimitiveOperation with poser_uid and list OUTPUT terminals
    only (they unbundle the data into field values at the input boundary).
    Recompose ops: PrimitiveOperation with poser_uid and list INPUT terminals
    only (they rebundle field values into the data at the output boundary).
    Regular ops: everything else (including ops with list terminals in BOTH
    directions, or poser_uid ops with no list terminals) — passed to
    generate_body() as normal.
    """
    decompose: list[PrimitiveOperation] = []
    recompose: list[PrimitiveOperation] = []
    regular: list[Operation] = []

    for op in inner:
        if not isinstance(op, PrimitiveOperation) or not op.poser_uid:
            regular.append(op)
            continue
        has_list_out = any(
            t.nmux_role == "list" and t.direction == "output" for t in op.terminals
        )
        has_list_in = any(
            t.nmux_role == "list" and t.direction == "input" for t in op.terminals
        )
        if has_list_out and not has_list_in:
            decompose.append(op)
        elif has_list_in and not has_list_out:
            recompose.append(op)
        else:
            regular.append(op)

    return decompose, recompose, regular

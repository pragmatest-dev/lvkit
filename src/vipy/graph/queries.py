"""Query mixin for InMemoryVIGraph.

Methods: get_inputs, get_outputs, get_constants, get_operations, get_wires,
get_operation_order, get_node, get_dataflow_graph, get_predecessors,
get_successors, get_source_of_output, get_vi_context, get_subvi_calls,
resolve_vi_name, list_vis, get_vi_source_path, is_stub_vi, get_stub_vi_info,
dependency graph queries, polymorphic VI methods,
query/query_single, get_all_constants/primitives/clusters.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

from ..graph_types import (
    AnyGraphNode,
    Constant,
    ConstantNode,
    FPTerminal,
    Operation,
    PolyInfo,
    StructureNode,
    Terminal,
    VIMetadata,
    VINode,
    Wire,
    WireEnd,
)
from ..graph_types import (
    PrimitiveNode as GraphPrimitiveNode,
)
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .core import _OPERATION_KINDS, _graph_node_to_op_kind

if TYPE_CHECKING:
    pass


class QueryMixin:
    """Mixin providing graph query methods."""

    # These attributes are defined on InMemoryVIGraph in core.py
    _graph: nx.MultiDiGraph
    _vi_nodes: dict[str, set[str]]
    _term_to_node: dict[str, str]
    _dep_graph: nx.DiGraph
    _stubs: set[str]
    _poly_info: dict[str, PolyInfo]
    _qualified_aliases: dict[str, str]
    _loaded_vis: set[str]
    _source_paths: dict[str, Path]
    _vi_metadata: dict[str, VIMetadata]

    # === Cypher query compat ===

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Cypher query compatibility - routes to native methods."""
        cypher_lower = cypher.lower()

        if "constant" in cypher_lower:
            return self.get_all_constants()
        elif "primitive" in cypher_lower:
            return self.get_all_primitives()
        elif "cluster" in cypher_lower:
            return self.get_all_clusters()

        return []

    def query_single(self, cypher: str, params: dict | None = None) -> dict | None:
        """Single-result Cypher query compatibility."""
        results = self.query(cypher, params)
        return results[0] if results else None

    def get_all_constants(self) -> list[dict[str, Any]]:
        """Get all constants across all VIs for enum discovery."""
        results = []
        for vi_name, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if not isinstance(gnode, ConstantNode):
                    continue
                results.append({
                    "vi_name": vi_name,
                    "value": gnode.raw_value or gnode.value or "",
                    "label": gnode.label,
                    "type": (
                        gnode.lv_type.underlying_type if gnode.lv_type else "Any"
                    ),
                    "python": gnode.value,
                })
        return results

    def get_all_primitives(self) -> list[dict[str, Any]]:
        """Get all primitives across all VIs for primitive discovery."""
        results = []
        for vi_name, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if not isinstance(gnode, GraphPrimitiveNode):
                    continue
                input_types = [
                    t.python_type()
                    for t in gnode.terminals
                    if t.direction == "input"
                ]
                output_types = [
                    t.python_type()
                    for t in gnode.terminals
                    if t.direction == "output"
                ]
                results.append({
                    "vi_name": vi_name,
                    "prim_id": gnode.prim_id,
                    "input_types": input_types,
                    "output_types": output_types,
                })
        return results

    def get_all_clusters(self) -> list[dict[str, Any]]:
        """Get all cluster types across all VIs for shared type discovery."""
        clusters: dict[str, set[str]] = {}

        for vi_name, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if not isinstance(gnode, VINode):
                    continue
                # Check FP terminals on VINodes for cluster types
                if gnode.vi != vi_name:
                    continue
                for term in gnode.terminals:
                    if isinstance(term, FPTerminal) and term.control_type == "stdClust":
                        name = term.name or "UnnamedCluster"
                        if name not in clusters:
                            clusters[name] = set()
                        clusters[name].add(vi_name)

        return [
            {"name": name, "id": name, "vis": list(vis)}
            for name, vis in clusters.items()
        ]

    # === Dependency Graph Queries ===

    def resolve_vi_name(self, vi_name: str) -> str:
        """Resolve a VI name to its canonical form.

        Handles both qualified names (MyLib.lvlib:VI.vi) and simple filenames.
        """
        if vi_name in self._vi_nodes:
            return vi_name
        if vi_name in self._qualified_aliases:
            return self._qualified_aliases[vi_name]
        if ":" in vi_name:
            simple_name = vi_name.split(":")[-1]
            if simple_name in self._vi_nodes:
                return simple_name
        return vi_name

    def list_vis(self) -> list[str]:
        """List all VIs in the graph (excluding stubs)."""
        return list(self._vi_nodes.keys())

    def get_vi_source_path(self, vi_name: str) -> Path | None:
        """Get the source file path for a VI."""
        return self._source_paths.get(vi_name)

    def is_stub_vi(self, vi_name: str) -> bool:
        """Check if a VI is a stub (missing dependency)."""
        return vi_name in self._stubs

    def get_stub_vi_info(self, vi_name: str) -> dict[str, Any] | None:
        """Get stub VI info from vilib reference or call site inference."""
        if vi_name not in self._stubs:
            return None

        # First, check vilib resolver for known VIs
        resolver = get_vilib_resolver()
        vilib_info = resolver.get_context(vi_name)
        if vilib_info:
            inputs = []
            outputs = []
            for t in vilib_info.get("terminals", []):
                term_type = t.get("type") or "Any"
                if t.get("direction") == "in":
                    inputs.append({"name": t["name"], "type": term_type})
                else:
                    outputs.append({"name": t["name"], "type": term_type})
            return {
                "name": vi_name,
                "vilib_path": vilib_info.get("vilib_path"),
                "python_hint": vilib_info.get("python"),
                "inputs": inputs,
                "outputs": outputs,
                "input_types": [i["type"] for i in inputs],
                "output_types": [o["type"] for o in outputs],
            }

        # Fall back to inferring from call site
        input_types: list[str] = []
        output_types: list[str] = []

        for caller_vi, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if (
                    isinstance(gnode, VINode)
                    and gnode.name == vi_name
                    and gnode.id != vi_name  # Not the VI definition itself
                ):
                    for term in gnode.terminals:
                        term_type = term.python_type()
                        if term_type == "unknown":
                            term_type = "Any"
                        if term.direction == "input":
                            input_types.append(term_type)
                        else:
                            output_types.append(term_type)
                    break
            if input_types or output_types:
                break

        return {
            "name": vi_name,
            "input_types": input_types,
            "output_types": output_types,
        }

    def get_vi_dependencies(self, vi_name: str) -> list[str]:
        """Get VIs that this VI depends on (SubVIs it calls)."""
        if vi_name not in self._dep_graph:
            return []
        return list(self._dep_graph.successors(vi_name))

    def get_vi_dependents(self, vi_name: str) -> list[str]:
        """Get VIs that depend on this VI (VIs that call it)."""
        if vi_name not in self._dep_graph:
            return []
        return list(self._dep_graph.predecessors(vi_name))

    def get_leaf_vis(self) -> list[str]:
        """Get VIs that don't call any SubVIs (leaves of dependency tree)."""
        return [
            n for n in self._dep_graph.nodes() if self._dep_graph.out_degree(n) == 0
        ]

    def has_cycles(self) -> bool:
        """Check if the dependency graph contains any cycles (recursive VIs)."""
        return not nx.is_directed_acyclic_graph(self._dep_graph)

    def get_cycles(self) -> list[list[str]]:
        """Detect and return all cycles in the VI dependency graph."""
        return list(nx.simple_cycles(self._dep_graph))

    def get_generation_order(self) -> Iterator[set[str]]:
        """Yield VI groups in dependency order.

        Returns sets of VI names. Each set can be generated together.
        Mutually recursive VIs are grouped in the same set.
        Dependencies come before dependents.
        """
        if not self._dep_graph.nodes():
            return

        condensation = nx.condensation(self._dep_graph)

        def scc_key(scc_id: int) -> str:
            return min(condensation.nodes[scc_id]["members"])

        scc_order = list(reversed(list(
            nx.lexicographical_topological_sort(condensation, key=scc_key)
        )))

        vilib_resolver = get_vilib_resolver()

        for scc_id in scc_order:
            members = condensation.nodes[scc_id]["members"]
            convertible_vis = {
                m for m in members
                if m not in self._stubs or vilib_resolver.has_implementation(m)
            }
            if convertible_vis:
                yield convertible_vis

    def get_conversion_order(self) -> list[str]:
        """Get VIs in topological order for bottom-up conversion."""
        result = []
        for group in self.get_generation_order():
            result.extend(sorted(group))
        return result

    # === Unified Graph Queries ===

    def _get_vi_nodes(self, vi_name: str) -> set[str]:
        """Get the set of node UIDs belonging to a VI."""
        return self._vi_nodes.get(vi_name, set())

    def _get_typed_node(self, uid: str) -> AnyGraphNode | None:
        """Get the typed Pydantic node model for a graph node."""
        if uid not in self._graph:
            return None
        return self._graph.nodes[uid].get("node")

    def get_dataflow_graph(self, vi_name: str) -> nx.DiGraph | None:
        """Get a subgraph view for a VI (backward compat).

        Returns a new DiGraph containing only nodes belonging to this VI,
        with edges between them. Used for backward compatibility.
        """
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return None

        # Build a DiGraph view from the unified MultiDiGraph
        sub = nx.DiGraph()

        for uid in node_uids:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            # Convert typed node to legacy dict format for backward compat
            sub.add_node(uid, **self._typed_node_to_legacy_dict(gnode))

        # Add edges between VI nodes
        for uid in node_uids:
            if uid not in self._graph:
                continue
            for _, dest, key, edata in self._graph.out_edges(uid, data=True, keys=True):
                if dest in node_uids and edata.get("vi") == vi_name:
                    sub.add_edge(uid, dest, **edata)

        return sub

    def _typed_node_to_legacy_dict(self, gnode: AnyGraphNode) -> dict[str, Any]:
        """Convert a typed graph node to the old dict format."""
        result: dict[str, Any] = {
            "name": gnode.name,
            "node_type": gnode.node_type,
        }

        if isinstance(gnode, VINode):
            # Could be a VI definition or SubVI call
            if gnode.id == gnode.vi:
                # VI definition — doesn't have a "kind" in the old sense
                result["kind"] = "vi_definition"
            else:
                result["kind"] = "vi"
            result["poly_variant_name"] = gnode.poly_variant_name
            result["terminals"] = [
                self._terminal_to_legacy_dict(t) for t in gnode.terminals
            ]
        elif isinstance(gnode, GraphPrimitiveNode):
            kind = "primitive"
            if gnode.node_type in ("caseStruct", "select"):
                kind = "caseStruct"
            elif gnode.node_type in ("whileLoop", "forLoop"):
                kind = "loop"
            result["kind"] = kind
            result["prim_id"] = gnode.prim_id
            result["prim_index"] = gnode.prim_index
            result["operation"] = gnode.operation
            result["object_name"] = gnode.object_name
            result["object_method_id"] = gnode.object_method_id
            result["properties"] = [
                {"name": p.name} for p in gnode.properties
            ]
            result["method_name"] = gnode.method_name
            result["method_code"] = gnode.method_code
            result["terminals"] = [
                self._terminal_to_legacy_dict(t) for t in gnode.terminals
            ]
        elif isinstance(gnode, StructureNode):
            if gnode.node_type in ("caseStruct", "select"):
                kind = "caseStruct"
            elif gnode.node_type in ("whileLoop", "forLoop"):
                kind = "loop"
            else:
                kind = "operation"
            result["kind"] = kind
            result["terminals"] = [
                self._terminal_to_legacy_dict(t) for t in gnode.terminals
            ]
        elif isinstance(gnode, ConstantNode):
            result["kind"] = "constant"
            result["value"] = gnode.value
            result["raw_value"] = gnode.raw_value
            result["label"] = gnode.label
            result["lv_type"] = gnode.lv_type

        if gnode.description:
            result["description"] = gnode.description

        return result

    @staticmethod
    def _terminal_to_legacy_dict(t: Terminal) -> dict[str, Any]:
        """Convert Terminal dataclass to legacy dict format."""
        d: dict[str, Any] = {
            "id": t.id,
            "index": t.index,
            "direction": t.direction,
            "type": t.python_type(),
            "name": t.name,
        }
        if t.lv_type:
            d["lv_type"] = t.lv_type
            if t.lv_type.typedef_path:
                d["typedef_path"] = t.lv_type.typedef_path
            if t.lv_type.typedef_name:
                d["typedef_name"] = t.lv_type.typedef_name
        return d

    def get_node(self, vi_name: str, node_id: str) -> dict[str, Any] | None:
        """Get a node's attributes from a VI's dataflow graph."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None or node_id not in node_uids:
            return None
        if node_id not in self._graph:
            return None
        gnode = self._graph.nodes[node_id].get("node")
        if gnode is None:
            return None
        return self._typed_node_to_legacy_dict(gnode)

    def get_inputs(
        self, vi_name: str, *, public_only: bool = True
    ) -> list[Terminal]:
        """Get VI input terminals.

        Reads from the VINode's terminal list (FPTerminal controls).
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._graph:
            return []

        gnode = self._graph.nodes[vi_name].get("node")
        if not isinstance(gnode, VINode):
            return []

        results = []
        for t in gnode.terminals:
            if t.direction != "input":
                continue
            if public_only and isinstance(t, FPTerminal) and not t.is_public:
                continue
            results.append(t)
        return results

    def get_outputs(
        self, vi_name: str, *, public_only: bool = True
    ) -> list[Terminal]:
        """Get VI output terminals.

        Reads from the VINode's terminal list (FPTerminal indicators).
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._graph:
            return []

        gnode = self._graph.nodes[vi_name].get("node")
        if not isinstance(gnode, VINode):
            return []

        results = []
        for t in gnode.terminals:
            if t.direction != "output":
                continue
            if public_only and isinstance(t, FPTerminal) and not t.is_public:
                continue
            results.append(t)
        return results

    def get_constants(self, vi_name: str) -> list[Constant]:
        """Get all constants in a VI."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        results = []
        for uid in node_uids:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if not isinstance(gnode, ConstantNode):
                continue
            results.append(Constant(
                id=gnode.id,
                value=gnode.value,
                lv_type=gnode.lv_type,
                raw_value=gnode.raw_value,
                name=gnode.label,
            ))
        return results

    def get_operations(self, vi_name: str) -> list[Operation]:
        """Get all operations (SubVIs, primitives) in a VI.

        Returns operations in dataflow execution order.
        Only returns top-level operations -- inner operations (parent != None)
        are nested inside their structure's inner_nodes/case_frames lists.
        """
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        # Top-level = parent is None (not inside any structure)
        top_level_op_uids: set[str] = set()
        for uid in node_uids:
            if uid == vi_name:
                continue
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            op_kind = _graph_node_to_op_kind(gnode)
            if op_kind in _OPERATION_KINDS and gnode.parent is None:
                top_level_op_uids.add(uid)

        # Get operations in dataflow order, keeping only top-level
        ordered_ids = [
            uid for uid in self.get_operation_order(vi_name)
            if uid in top_level_op_uids
        ]
        op_set = set(ordered_ids)

        # Add any top-level ops not in the sorted order (disconnected)
        for uid in top_level_op_uids:
            if uid not in op_set:
                ordered_ids.append(uid)

        return [
            self._build_operation(uid, vi_name)
            for uid in ordered_ids
            if uid in self._graph
        ]

    def get_operation_order(self, vi_name: str) -> list[str]:
        """Get top-level operations in dataflow execution order.

        Returns operation node IDs in the order they should execute
        (topological sort based on wire connections).

        Only includes top-level operations (parent=None). Nested operations
        (inside structures like flat/stacked sequences, loops, cases) are
        handled by their parent structure's codegen — including them here
        creates cycles (structure ↔ child edges) that break topological sort.
        """
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        # Get top-level operation node IDs only
        op_ids: set[str] = set()
        for uid in node_uids:
            if uid == vi_name:
                continue  # Skip VINode itself
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            op_kind = _graph_node_to_op_kind(gnode)
            if op_kind in _OPERATION_KINDS and gnode.parent is None:
                op_ids.add(uid)

        if not op_ids:
            return []

        # Build operation-level dependency graph from edges
        op_deps = nx.DiGraph()
        op_deps.add_nodes_from(op_ids)

        for uid in op_ids:
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if dest in op_ids and dest != uid:
                    op_deps.add_edge(uid, dest)

        try:
            return list(nx.topological_sort(op_deps))
        except nx.NetworkXUnfeasible:
            return list(op_ids)

    def get_predecessors(self, vi_name: str, node_id: str) -> list[str]:
        """Get nodes that feed into this node (direct predecessors)."""
        if node_id not in self._graph:
            return []
        return list(self._graph.predecessors(node_id))

    def get_successors(self, vi_name: str, node_id: str) -> list[str]:
        """Get nodes that this node feeds into (direct successors)."""
        if node_id not in self._graph:
            return []
        return list(self._graph.successors(node_id))

    def get_source_of_output(self, vi_name: str, output_id: str) -> str | None:
        """Trace an output terminal back to its source node.

        Returns the ID of the node that produces the value for this output.
        """
        # In the unified graph, output_id is a terminal on the VINode.
        # Find direct predecessors.
        if vi_name not in self._graph:
            return None

        preds = list(self._graph.predecessors(vi_name))
        if not preds:
            return None

        # Check if any predecessor has an edge whose dest terminal matches
        for pred in preds:
            for _, _, edata in self._graph.edges(pred, data=True):
                dest_end = edata.get("dest")
                if dest_end and dest_end.terminal_id == output_id:
                    return pred

        # Fall back to first predecessor
        return preds[0] if preds else None

    def get_wires(self, vi_name: str) -> list[Wire]:
        """Get all wires (edges) in a VI's dataflow graph.

        Returns Wire objects with typed WireEnd source/dest.
        Includes internal edges (self-loops on structure nodes for
        tunnel outer<->inner and sRN input->output pairings).
        """
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        # Collect edges: tunnel/internal edges first, then normal edges
        tunnel_edges: list[Wire] = []
        normal_edges: list[Wire] = []

        for uid in node_uids:
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if edata.get("vi") != vi_name:
                    continue

                src_end = edata.get("source")
                dst_end = edata.get("dest")

                if src_end is None or dst_end is None:
                    continue

                # Look up parent node data for labels and names
                from_node = self._get_typed_node(src_end.node_id)
                to_node = self._get_typed_node(dst_end.node_id)

                from_kind = _graph_node_to_op_kind(from_node) if from_node else ""
                to_kind = _graph_node_to_op_kind(to_node) if to_node else ""
                from_labels = self._kind_to_labels(from_kind)
                to_labels = self._kind_to_labels(to_kind)

                wire = Wire(
                    source=WireEnd(
                        terminal_id=src_end.terminal_id,
                        node_id=src_end.node_id,
                        index=src_end.index,
                        name=from_node.name if from_node else None,
                        labels=from_labels,
                    ),
                    dest=WireEnd(
                        terminal_id=dst_end.terminal_id,
                        node_id=dst_end.node_id,
                        index=dst_end.index,
                        name=to_node.name if to_node else None,
                        labels=to_labels,
                    ),
                )

                if edata.get("tunnel_type"):
                    tunnel_edges.append(wire)
                else:
                    normal_edges.append(wire)

        return tunnel_edges + normal_edges

    # === Legacy API ===

    def get_vi_context(self, vi_name: str) -> dict[str, Any]:
        """Get complete VI context for code generation.

        Returns a dict with inputs, outputs, constants, operations, etc.
        Builds from typed graph nodes.
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._vi_nodes:
            return {}

        # Build subvi_calls list
        subvi_calls = []
        for uid in self._vi_nodes[vi_name]:
            if uid == vi_name:
                continue
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if isinstance(gnode, VINode) and gnode.id != gnode.vi:
                subvi_calls.append({
                    "call_name": gnode.name,
                    "vi_name": gnode.name,
                })

        # Build terminals list for skeleton generator (legacy)
        terminals = []
        for uid in self._vi_nodes[vi_name]:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            for t in gnode.terminals:
                terminals.append({
                    "id": t.id,
                    "parent_id": gnode.id,
                    "index": t.index,
                    "type": t.python_type(),
                    "name": t.name,
                    "direction": t.direction,
                })

        inputs = list(self.get_inputs(vi_name))
        outputs = list(self.get_outputs(vi_name))
        constants = list(self.get_constants(vi_name))
        operations = list(self.get_operations(vi_name))
        data_flow = list(self.get_wires(vi_name))

        vi_meta = self._vi_metadata.get(vi_name, VIMetadata())

        return {
            "name": vi_name,
            "library": vi_meta.library,
            "qualified_name": vi_meta.qualified_name,
            "inputs": inputs,
            "outputs": outputs,
            "constants": constants,
            "operations": operations,
            "terminals": terminals,
            "data_flow": data_flow,
            "subvi_calls": subvi_calls,
            "poly_variants": self.get_poly_variants(vi_name),
            "has_parallel_branches": self.has_parallel_branches(vi_name),
        }

    def get_subvi_calls(self, vi_name: str) -> list[dict]:
        """Get SubVIs called by a VI."""
        ctx = self.get_vi_context(vi_name)
        return ctx.get("subvi_calls", [])

    # === Polymorphic VI Methods ===

    def is_polymorphic(self, vi_name: str) -> bool:
        """Check if a VI is a polymorphic wrapper."""
        return vi_name in self._poly_info

    def get_poly_variants(self, vi_name: str) -> list[str]:
        """Get variants for a polymorphic VI."""
        info = self._poly_info.get(vi_name)
        return info.variants if info else []

    def get_polymorphic_groups(self) -> dict[str, list[str]]:
        """Get all polymorphic VIs and their variants."""
        return {
            vi_name: info.variants
            for vi_name, info in self._poly_info.items()
            if info.variants
        }

    def get_poly_variant_wrappers(self) -> dict[str, str]:
        """Get mapping of variant VI names to their wrapper VI."""
        result: dict[str, str] = {}
        for wrapper, info in self._poly_info.items():
            for variant in info.variants:
                result[variant] = wrapper
        return result

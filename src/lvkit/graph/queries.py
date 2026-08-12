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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

from ..models import ClusterField, FPTerminal, Operation, Terminal
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .core import _OPERATION_KINDS, _graph_node_to_op_kind, _node_order_key
from .models import (
    AnyGraphNode,
    ClassFieldEntry,
    ClassHierarchyInfo,
    ClusterInfo,
    Constant,
    ConstantInfo,
    ConstantNode,
    MethodAccessInfo,
    MethodOverrideInfo,
    PolyInfo,
    PrimitiveInfo,
    StructureNode,
    StubTerminalInfo,
    StubVIInfo,
    SubVICall,
    TerminalRef,
    VIContext,
    VIHealth,
    VIMetadata,
    VINode,
    VIProperties,
    Wire,
    WireEnd,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
)

if TYPE_CHECKING:
    from ..parser.layout import Layout
    from .core import InMemoryVIGraph


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
    _vi_properties: dict[str, VIProperties]
    _vi_health: dict[str, VIHealth]
    _layouts: dict[str, Layout]
    _search_paths: list[Path]
    _vilib_root: Path | None
    _userlib_root: Path | None
    _vi_file_index: dict[str, Path] | None

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins / core, resolved via MRO
        def _build_operation(self, uid: str, vi_name: str) -> Operation: ...
        def has_parallel_branches(self, vi_name: str) -> bool: ...
        def get_class_fields(
            self, classname: str,
        ) -> list[ClusterField] | None: ...

    # === Cypher query compat ===

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Cypher query compatibility - routes to native methods.

        Returns dicts for backward compatibility with legacy consumers.
        """
        cypher_lower = cypher.lower()

        if "constant" in cypher_lower:
            return [asdict(c) for c in self.get_all_constants()]
        elif "primitive" in cypher_lower:
            return [asdict(p) for p in self.get_all_primitives()]
        elif "cluster" in cypher_lower:
            return [asdict(c) for c in self.get_all_clusters()]

        return []

    def query_single(self, cypher: str, params: dict | None = None) -> dict | None:
        """Single-result Cypher query compatibility."""
        results = self.query(cypher, params)
        return results[0] if results else None

    def get_all_constants(self) -> list[ConstantInfo]:
        """Get all constants across all VIs for enum discovery."""
        results: list[ConstantInfo] = []
        for vi_name, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if not isinstance(gnode, ConstantNode):
                    continue
                _const_value: str = (
                    gnode.raw_value
                    or (str(gnode.value) if gnode.value is not None else "")
                )
                results.append(ConstantInfo(
                    vi_name=vi_name,
                    value=_const_value,
                    label=gnode.label,
                    type=(
                        (gnode.lv_type.underlying_type or "Any")
                        if gnode.lv_type
                        else "Any"
                    ),
                    python=gnode.value,
                ))
        return results

    def get_all_primitives(self) -> list[PrimitiveInfo]:
        """Get all primitives across all VIs for primitive discovery."""
        results: list[PrimitiveInfo] = []
        for vi_name, node_uids in self._vi_nodes.items():
            for uid in node_uids:
                if uid not in self._graph:
                    continue
                gnode = self._graph.nodes[uid].get("node")
                if not isinstance(gnode, GraphPrimitiveNode):
                    continue
                input_types = [
                    t.lv_type.lv_label() if t.lv_type else "Any"
                    for t in gnode.terminals
                    if t.direction == "input"
                ]
                output_types = [
                    t.lv_type.lv_label() if t.lv_type else "Any"
                    for t in gnode.terminals
                    if t.direction == "output"
                ]
                results.append(PrimitiveInfo(
                    vi_name=vi_name,
                    prim_id=gnode.prim_id,
                    input_types=input_types,
                    output_types=output_types,
                ))
        return results

    def get_all_clusters(self) -> list[ClusterInfo]:
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
            ClusterInfo(name=name, id=name, vis=list(vis))
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

    def locate_vi_file(self, vi_name: str) -> Path | None:
        """Best-effort on-disk ``.vi`` for a SubVI by name: an already-loaded
        source first, else a filename search of the graph's retained search
        paths. Decoration-only (SubVI icons) — never forces a load. Under a
        MINIMAL load the SubVIs aren't in ``_source_paths``, so this filename
        search is what lets a project-local SubVI's own ``_ICON.png`` resolve.
        The search-path index is built once, lazily, and cached."""
        loaded = self._source_paths.get(vi_name)
        if loaded is not None:
            return loaded
        if self._vi_file_index is None:
            index: dict[str, Path] = {}
            for root in self._search_paths:
                try:
                    for found in sorted(root.rglob("*.vi")):
                        index.setdefault(found.name, found)
                except OSError:
                    continue
            self._vi_file_index = index
        return self._vi_file_index.get(vi_name)

    def resolve_library_vi_path(self, qualified_path: str | None) -> Path | None:
        """Resolve a ``<vilib>``/``<userlib>`` token path to a real ``.vi``
        under the user's LOCAL library roots (``set_library_roots`` from
        ``--vilib``/``--userlib`` or auto-detect). Decoration-only (icons).

        Rendering a user's own licensed vi.lib icon on their own machine is
        NOT distribution — lvkit never ships this art, and a hosted service
        simply has no roots set (falls through to None). Loose ``.vi`` only:
        a member packed inside a ``.llb`` isn't addressable as a file and
        returns None (deferred). Token order mirrors the loader's
        ``path_tokens`` (``["<vilib>", "Utility", ...]``)."""
        if not qualified_path:
            return None
        for token, root in (
            ("<vilib>", self._vilib_root),
            ("<userlib>", self._userlib_root),
        ):
            if qualified_path.startswith(token) and root is not None:
                rel = qualified_path[len(token):].lstrip("/\\")
                if ".llb/" in rel.replace("\\", "/").lower():
                    return None  # packed member — needs archive extraction
                candidate = root / rel
                return candidate if candidate.is_file() else None
        return None

    def get_layout(self, vi_name: str) -> Layout | None:
        """Get a VI's block-diagram geometry, or None if the graph was not
        loaded with ``layout=True``. Populated from the same parse (no second
        heap read); the renderer consumes this instead of re-reading the XML."""
        return self._layouts.get(self.resolve_vi_name(vi_name))

    def is_stub_vi(self, vi_name: str) -> bool:
        """Check if a VI is a stub (missing dependency)."""
        return vi_name in self._stubs

    def get_stub_vi_info(self, vi_name: str) -> StubVIInfo | None:
        """Get stub VI info from vilib reference or call site inference."""
        if vi_name not in self._stubs:
            return None

        # First, check vilib resolver for known VIs
        resolver = get_vilib_resolver()
        vilib_info = resolver.get_context(vi_name)
        if vilib_info:
            inputs: list[StubTerminalInfo] = []
            outputs: list[StubTerminalInfo] = []
            for t in vilib_info.get("terminals", []):
                term_type = t.get("type") or "Any"
                if t.get("direction") == "in":
                    inputs.append(StubTerminalInfo(name=t["name"], type=term_type))
                else:
                    outputs.append(StubTerminalInfo(name=t["name"], type=term_type))
            return StubVIInfo(
                name=vi_name,
                vilib_path=vilib_info.get("vilib_path"),
                python_hint=vilib_info.get("python"),
                inputs=inputs,
                outputs=outputs,
                input_types=[i.type for i in inputs],
                output_types=[o.type for o in outputs],
            )

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
                        term_type = (
                            term.lv_type.lv_label() if term.lv_type else "Any"
                        )
                        if term_type == "unknown":
                            term_type = "Any"
                        if term.direction == "input":
                            input_types.append(term_type)
                        else:
                            output_types.append(term_type)
                    break
            if input_types or output_types:
                break

        return StubVIInfo(
            name=vi_name,
            input_types=input_types,
            output_types=output_types,
        )

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
            result["kind"] = "primitive"
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
            result["kind"] = _graph_node_to_op_kind(gnode)
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
            "type": t.lv_type.lv_label() if t.lv_type else "Any",
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
        for uid in sorted(node_uids, key=_node_order_key):
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if not isinstance(gnode, ConstantNode):
                continue
            results.append(Constant(
                id=gnode.id,
                value=gnode.value,
                lv_type=gnode.lv_type,
                display_format=gnode.display_format,
                raw_value=gnode.raw_value,
                label=gnode.label,
                parent=gnode.parent,
                frame=gnode.frame,
            ))
        return results

    def get_operations(self, vi_name: str) -> list[Operation]:
        """Get all operations (SubVIs, primitives) in a VI.

        Returns operations in dataflow execution order.
        Only returns top-level operations -- inner operations (parent != None)
        are nested inside their structure's inner_nodes/frames lists.
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

        # Add any top-level ops not in the sorted order (disconnected) in a
        # deterministic order — top_level_op_uids is a hash-randomized set.
        for uid in sorted(top_level_op_uids, key=_node_order_key):
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

        # Build operation-level dependency graph from edges. Insert nodes in
        # a deterministic order (op_ids is a hash-randomized set) and break
        # topological-sort ties with the same key, so independent operations
        # land in a stable order — codegen output must be reproducible.
        ordered_ids = sorted(op_ids, key=_node_order_key)
        op_deps = nx.DiGraph()
        op_deps.add_nodes_from(ordered_ids)

        for uid in ordered_ids:
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if dest in op_ids and dest != uid:
                    op_deps.add_edge(uid, dest)

        try:
            return list(
                nx.lexicographical_topological_sort(op_deps, key=_node_order_key)
            )
        except nx.NetworkXUnfeasible:
            return ordered_ids

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

    def get_wires(
        self, vi_name: str, include_internal: bool = False,
    ) -> list[Wire]:
        """Get all wires (edges) in a VI's dataflow graph.

        Returns Wire objects with typed WireEnd source/dest.

        An edge is *internal* when it's a structure self-loop (tunnel
        outer<->inner or sRN input->output pairing) — detected as
        ``source.node_id == dest.node_id`` and/or a truthy ``tunnel_type``
        edge attribute. By default internal edges are excluded (renderers
        want external wires only); pass ``include_internal=True`` to get
        the full set, with internal edges first (legacy behavior).
        """
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        # Collect edges: tunnel/internal edges first, then normal edges.
        # node_uids is a hash-randomized set — sort for byte-reproducible
        # output (codegen and the renderer both depend on stable ordering).
        tunnel_edges: list[Wire] = []
        normal_edges: list[Wire] = []

        for uid in sorted(node_uids, key=_node_order_key):
            if uid not in self._graph:
                continue
            for _, dest, edata in self._graph.out_edges(uid, data=True):
                if edata.get("vi") != vi_name:
                    continue

                src_end = edata.get("source")
                dst_end = edata.get("dest")

                if src_end is None or dst_end is None:
                    continue

                is_internal = (
                    bool(edata.get("tunnel_type"))
                    or src_end.node_id == dst_end.node_id
                )
                if is_internal and not include_internal:
                    continue

                # Look up parent node data for classification kind and names
                from_node = self._get_typed_node(src_end.node_id)
                to_node = self._get_typed_node(dst_end.node_id)

                from_kind = _graph_node_to_op_kind(from_node) if from_node else ""
                to_kind = _graph_node_to_op_kind(to_node) if to_node else ""

                wire = Wire(
                    source=WireEnd(
                        terminal_id=src_end.terminal_id,
                        node_id=src_end.node_id,
                        index=src_end.index,
                        name=from_node.name if from_node else None,
                        parent_kind=from_kind or None,
                    ),
                    dest=WireEnd(
                        terminal_id=dst_end.terminal_id,
                        node_id=dst_end.node_id,
                        index=dst_end.index,
                        name=to_node.name if to_node else None,
                        parent_kind=to_kind or None,
                    ),
                )

                if is_internal:
                    tunnel_edges.append(wire)
                else:
                    normal_edges.append(wire)

        return tunnel_edges + normal_edges

    def iter_nodes(self, vi_name: str) -> list[AnyGraphNode]:
        """List a VI's graph nodes, excluding the VI-definition node itself.

        The VI-definition node's id equals ``vi_name`` and has no diagram
        geometry — callers that need per-node rendering/geometry joins want
        everything else. Sorted by ``_node_order_key`` for deterministic,
        byte-reproducible output (``_vi_nodes`` is a hash-randomized set).
        """
        vi_name = self.resolve_vi_name(vi_name)
        node_uids = self._vi_nodes.get(vi_name)
        if not node_uids:
            return []
        result: list[AnyGraphNode] = []
        for uid in sorted(node_uids, key=_node_order_key):
            if uid == vi_name:
                continue
            gnode = self._get_typed_node(uid)
            if gnode is not None:
                result.append(gnode)
        return result

    def get_terminal(self, terminal_id: str) -> Terminal | None:
        """Look up a single Terminal by its fully qualified id.

        Uses ``_term_to_node`` to find the owning node, then scans its
        terminal list for the matching id. Returns None if the terminal
        (or its node) isn't in the graph.
        """
        node_id = self._term_to_node.get(terminal_id)
        if node_id is None:
            return None
        gnode = self._get_typed_node(node_id)
        if gnode is None:
            return None
        for t in gnode.terminals:
            if t.id == terminal_id:
                return t
        return None

    # === Legacy API ===

    def get_vi_properties(self, vi_name: str) -> VIProperties:
        """The VI's Properties facet (Protection/Execution/Window/…) alone --
        no full ``VIContext`` build. Same source ``get_vi_context`` reads
        (``_vi_properties``); for callers that only need this one facet
        (e.g. ``index/build.py``)."""
        vi_name = self.resolve_vi_name(vi_name)
        return self._vi_properties.get(vi_name, VIProperties())

    def get_vi_health(self, vi_name: str) -> VIHealth:
        """The VI's Health facet (compile-health) alone -- see
        ``get_vi_properties``."""
        vi_name = self.resolve_vi_name(vi_name)
        return self._vi_health.get(vi_name, VIHealth())

    def get_vi_context(self, vi_name: str) -> VIContext:
        """Get complete VI context for code generation.

        Returns a VIContext with inputs, outputs, constants, operations, etc.
        Builds from typed graph nodes.
        """
        vi_name = self.resolve_vi_name(vi_name)
        if vi_name not in self._vi_nodes:
            return VIContext(name=vi_name)

        # Build subvi_calls list
        subvi_calls: list[SubVICall] = []
        for uid in self._vi_nodes[vi_name]:
            if uid == vi_name:
                continue
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if isinstance(gnode, VINode) and gnode.id != gnode.vi:
                subvi_calls.append(SubVICall(
                    call_name=gnode.name,
                    vi_name=gnode.name,
                ))

        # Build terminals list for skeleton generator
        terminals: list[TerminalRef] = []
        for uid in self._vi_nodes[vi_name]:
            if uid not in self._graph:
                continue
            gnode = self._graph.nodes[uid].get("node")
            if gnode is None:
                continue
            for t in gnode.terminals:
                terminals.append(TerminalRef(
                    id=t.id,
                    parent_id=gnode.id,
                    index=t.index,
                    type=t.lv_type.lv_label() if t.lv_type else "Any",
                    name=t.name,
                    direction=t.direction,
                ))

        inputs = list(self.get_inputs(vi_name))
        outputs = list(self.get_outputs(vi_name))
        constants = list(self.get_constants(vi_name))
        operations = list(self.get_operations(vi_name))
        data_flow = list(self.get_wires(vi_name))

        vi_meta = self._vi_metadata.get(vi_name, VIMetadata())

        return VIContext(
            name=vi_name,
            library=vi_meta.library,
            qualified_name=vi_meta.qualified_name,
            inputs=inputs,
            outputs=outputs,
            constants=constants,
            operations=operations,
            terminals=terminals,
            data_flow=data_flow,
            subvi_calls=subvi_calls,
            poly_variants=self.get_poly_variants(vi_name),
            has_parallel_branches=self.has_parallel_branches(vi_name),
            properties=self._vi_properties.get(vi_name, VIProperties()),
            health=self._vi_health.get(vi_name, VIHealth()),
        )

    def get_subvi_calls(self, vi_name: str) -> list[SubVICall]:
        """Get SubVIs called by a VI."""
        ctx = self.get_vi_context(vi_name)
        return ctx.subvi_calls

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

    # === Class Hierarchy Queries ===
    #
    # These read the class/method structure that ``load_lvclass()`` records
    # on ``_dep_graph``: class nodes carry ``parent_class`` (bare ancestor
    # name) + ``fields`` (own private-data fields), and "owns" edges from a
    # class to its method VIs carry the per-method scope/accessor info
    # parsed by ``structure.parse_lvclass()``.

    def list_classes(self) -> list[str]:
        """List all loaded (non-stub) class names in the dependency graph."""
        return sorted(
            n for n, d in self._dep_graph.nodes(data=True)
            if d.get("node_type") == "class" and n not in self._stubs
        )

    def get_owning_class(self, vi_name: str) -> str | None:
        """Get the class that owns this method VI, via its "owns" edge.

        Returns None if ``vi_name`` isn't a class method VI.
        """
        if vi_name not in self._dep_graph:
            return None
        for pred in self._dep_graph.predecessors(vi_name):
            if self._dep_graph.nodes[pred].get("node_type") != "class":
                continue
            edata = self._dep_graph.get_edge_data(pred, vi_name) or {}
            if edata.get("rel") == "owns":
                return pred
        return None

    def get_owning_library(self, vi_name: str) -> str | None:
        """Get the ``.lvlib`` that owns this VI directly, via its "owns" edge.

        Exact mirror of ``get_owning_class`` but for ``node_type == "library"``
        predecessors — ``load_lvlib`` records a library node + an "owns" edge
        to each ``Type="VI"`` member the same way ``load_lvclass`` does for
        methods (see ``loading.py``). Returns None if ``vi_name`` isn't a
        library member VI (or the library was never loaded).
        """
        if vi_name not in self._dep_graph:
            return None
        for pred in self._dep_graph.predecessors(vi_name):
            if self._dep_graph.nodes[pred].get("node_type") != "library":
                continue
            edata = self._dep_graph.get_edge_data(pred, vi_name) or {}
            if edata.get("rel") == "owns":
                return pred
        return None

    def get_class_hierarchy(self, classname: str) -> ClassHierarchyInfo | None:
        """Get hierarchy info for one loaded class: parent, children,
        documented methods, and fields (own + inherited).

        Returns None if ``classname`` isn't a loaded (non-stub) class node.
        """
        if not self._dep_graph.has_node(classname) or classname in self._stubs:
            return None
        data = self._dep_graph.nodes[classname]
        if data.get("node_type") != "class":
            return None

        # parent_class is recorded as the bare ancestor name (no ".lvclass",
        # no library qualification — see structure._parent_from_link_info).
        # Re-qualify and only surface it if that class is itself loaded — this
        # is the DOCS contract (html_generator links the parent page, so a
        # not-loaded parent must not dangle). The INDEX, which wants the
        # authoritative parent regardless of what's loaded, uses
        # ``get_class_parent`` instead.
        parent_raw: str | None = data.get("parent_class")
        parent_class: str | None = None
        if parent_raw and parent_raw != "LabVIEW Object":
            candidate = parent_raw + ".lvclass"
            if self._dep_graph.has_node(candidate) and candidate not in self._stubs:
                parent_class = candidate

        # Children: invert parent_class across every loaded class. Compare
        # against the bare (unqualified) leaf of our own name, matching how
        # parent_class is recorded.
        leaf = classname.rsplit(":", 1)[-1]
        own_bare = leaf[: -len(".lvclass")] if leaf.endswith(".lvclass") else leaf
        child_classes = sorted(
            node for node, ndata in self._dep_graph.nodes(data=True)
            if ndata.get("node_type") == "class"
            and node not in self._stubs
            and ndata.get("parent_class") == own_bare
        )

        # Methods: VI nodes owned by this class that are actually documented
        # (present in list_vis() — excludes stub/unresolved method VIs).
        vis = set(self.list_vis())
        methods = sorted(
            succ for succ in self._dep_graph.successors(classname)
            if succ in vis
            and (self._dep_graph.get_edge_data(classname, succ) or {}).get("rel")
            == "owns"
        )

        own_fields: list[ClusterField] = data.get("fields") or []
        inherited_fields: list[ClusterField] = (
            self.get_class_fields(parent_class) or [] if parent_class else []
        )
        fields = [
            ClassFieldEntry(field=f, inherited=True) for f in inherited_fields
        ] + [
            ClassFieldEntry(field=f, inherited=False) for f in own_fields
        ]

        return ClassHierarchyInfo(
            classname=classname,
            parent_class=parent_class,
            child_classes=child_classes,
            methods=methods,
            fields=fields,
        )

    def get_class_parent(self, classname: str) -> str | None:
        """The AUTHORITATIVE parent class of ``classname`` (bare name +
        ``.lvclass``), or None if it's a root / not a loaded class node.

        Unlike ``get_class_hierarchy``, this does NOT gate on the parent being
        loaded: ``parent_class`` is decoded straight from the class file's
        ``NI.LVClass.ParentClassLinkInfo`` (``structure._parent_from_link_info``),
        so it is correct even in a single-VI collision-load graph
        (``index/build.build_one_vi``) that never loads the parent. The index
        uses this so a method's ``class_fact.parent`` is stable across load
        paths — no spurious NULL beside the real value (the WaitOnTestComplete
        duplicate). Docs keep the link-safe, load-gated ``get_class_hierarchy``.
        """
        if not self._dep_graph.has_node(classname) or classname in self._stubs:
            return None
        data = self._dep_graph.nodes[classname]
        if data.get("node_type") != "class":
            return None
        parent_raw = data.get("parent_class")
        if parent_raw and parent_raw != "LabVIEW Object":
            return parent_raw + ".lvclass"
        return None

    def get_class_version(self, classname: str) -> str | None:
        """The class's ``NI.Lib.Version`` (dotted-quad string), or None if
        it's a not-loaded/stub class node or the property was absent.

        Straight passthrough of ``structure.LVClass.version``, recorded on
        the class node at load time (``loading.load_lvclass``).
        """
        if not self._dep_graph.has_node(classname) or classname in self._stubs:
            return None
        data = self._dep_graph.nodes[classname]
        if data.get("node_type") != "class":
            return None
        version = data.get("version")
        return version if isinstance(version, str) else None

    def get_class_ancestors(self, classname: str) -> list[str]:
        """The class's FULL ancestor chain, nearest-first, or ``[]`` if it's a
        not-loaded/stub class node or no ancestor's file resolved.

        Straight passthrough of ``structure.LVClass.ancestors`` — a best-effort
        on-disk resolution done at parse time (see
        ``structure._build_ancestor_chain``); may be a PREFIX of the true
        chain when an ancestor's ``.lvclass`` isn't present in this checkout.
        """
        if not self._dep_graph.has_node(classname) or classname in self._stubs:
            return []
        data = self._dep_graph.nodes[classname]
        if data.get("node_type") != "class":
            return []
        ancestors = data.get("ancestors")
        return list(ancestors) if ancestors else []

    def get_method_access(self, vi_name: str) -> MethodAccessInfo | None:
        """Get access-scope info for a class method VI.

        Returns None if ``vi_name`` isn't a class method VI, or its "owns"
        edge predates the scope/accessor attrs (should not happen for
        anything loaded via ``load_lvclass()``).
        """
        owner = self.get_owning_class(vi_name)
        if owner is None:
            return None
        edata = self._dep_graph.get_edge_data(owner, vi_name) or {}
        scope = edata.get("scope")
        if scope is None:
            return None
        return MethodAccessInfo(
            vi_name=vi_name,
            scope=scope,
            is_accessor=bool(edata.get("is_accessor")),
            accessor_type=edata.get("accessor_type"),
            accessor_field=edata.get("accessor_field"),
            is_static=bool(edata.get("is_static")),
            must_override=bool(edata.get("must_override")),
            must_call_parent=bool(edata.get("must_call_parent")),
        )

    def get_method_overrides(self, vi_name: str) -> MethodOverrideInfo | None:
        """Get bidirectional override links for a class method VI.

        Matches by bare method name (e.g. "run.vi") against the immediate
        parent class and each immediate child class. Only links to VIs that
        are themselves documented (in ``list_vis()``). Returns None if
        ``vi_name`` isn't a class method VI, or no override link exists in
        either direction.
        """
        owner = self.get_owning_class(vi_name)
        if owner is None:
            return None
        hierarchy = self.get_class_hierarchy(owner)
        if hierarchy is None:
            return None

        bare_method = vi_name.rsplit(":", 1)[-1]
        vis = set(self.list_vis())

        overrides: str | None = None
        if hierarchy.parent_class:
            candidate = f"{hierarchy.parent_class}:{bare_method}"
            if candidate in vis:
                overrides = candidate

        overridden_by: list[str] = []
        for child in hierarchy.child_classes:
            candidate = f"{child}:{bare_method}"
            if candidate in vis:
                overridden_by.append(candidate)
        overridden_by.sort()

        if overrides is None and not overridden_by:
            return None

        return MethodOverrideInfo(
            vi_name=vi_name,
            overrides=overrides,
            overridden_by=overridden_by,
        )


@dataclass(frozen=True)
class ClassContext:
    """The owning ``.lvclass``'s context for a class-method VI -- the ONE
    shared collection of facts both ``describe.py``'s ``## Class`` section
    and ``netlist.py``'s ``NetlistModule.class_context`` (the ``get_context``/
    MCP JSON IR, which carries this dataclass DIRECTLY -- no netlist-local
    wrapper) render, via ``collect_class_context`` -- so the two surfaces
    can't drift on what "the class context" means."""

    owning_class: str
    parent: str | None
    version: str | None
    ancestors: list[str]
    scope: str | None
    is_static: bool
    must_override: bool
    must_call_parent: bool


def collect_class_context(
    graph: InMemoryVIGraph, ctx: VIContext,
) -> ClassContext | None:
    """The owning class's context when ``ctx`` is a ``.lvclass`` method VI,
    else None -- the single source both ``describe._describe_class_context``
    and ``netlist._build_class_context`` build on."""
    cls = ctx.library
    if not cls or not cls.endswith(".lvclass"):
        return None
    if not graph._dep_graph.has_node(cls):
        return None

    parent = graph._dep_graph.nodes[cls].get("parent_class")
    access = graph.get_method_access(ctx.name)
    return ClassContext(
        owning_class=cls,
        parent=parent,
        version=graph.get_class_version(cls),
        ancestors=graph.get_class_ancestors(cls),
        scope=access.scope if access else None,
        is_static=bool(access and access.is_static),
        must_override=bool(access and access.must_override),
        must_call_parent=bool(access and access.must_call_parent),
    )

"""In-memory graph using NetworkX instead of Neo4j.

Two-graph architecture:
1. Dependency graph: VI -> SubVI relationships for processing order
2. Dataflow graphs: Per-VI operation/wire graphs for execution order

No database server required. Supports recursive VIs via SCC detection.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import networkx as nx

from .cypher import extract_vi_xml
from .parser import BlockDiagram, parse_block_diagram, parse_subvi_paths


class InMemoryVIGraph:
    """In-memory VI graph using NetworkX.

    Usage:
        graph = InMemoryVIGraph()
        graph.load_vi("/path/to/Main.vi", expand_subvis=True)

        # Process VIs in dependency order (handles recursive VIs)
        for vi_group in graph.get_generation_order():
            for vi_name in vi_group:
                # Get operation execution order
                for op_id in graph.get_operation_order(vi_name):
                    op = graph.get_node(vi_name, op_id)
                    # ... generate code ...
    """

    def __init__(self):
        # Dependency graph: VI name -> VI name (caller -> callee)
        self._dep_graph: nx.DiGraph = nx.DiGraph()
        # Per-VI dataflow graphs: vi_name -> DiGraph of operations/constants/terminals
        self._dataflow: dict[str, nx.DiGraph] = {}
        # Stub VIs (missing dependencies)
        self._stubs: set[str] = set()

    def clear(self) -> None:
        """Clear all loaded data."""
        self._dep_graph.clear()
        self._dataflow.clear()
        self._stubs.clear()

    # === Loading ===

    def load_vi(
        self,
        vi_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
        clear_first: bool = False,
    ) -> None:
        """Load a VI hierarchy into memory.

        Args:
            vi_path: Path to .vi file or *_BDHb.xml file
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
            clear_first: Clear existing data before loading
        """
        vi_path = Path(vi_path)

        if clear_first:
            self.clear()

        # Handle .vi files by extracting first
        if vi_path.suffix.lower() == ".vi":
            bd_xml, _, main_xml = extract_vi_xml(vi_path)
        elif vi_path.name.endswith("_BDHb.xml"):
            bd_xml = vi_path
            # Try to find main XML
            main_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", ".xml"))
            if not main_xml.exists():
                main_xml = None
        else:
            raise ValueError(f"Expected .vi or *_BDHb.xml file: {vi_path}")

        # Build search paths
        if search_paths is None:
            search_paths = [vi_path.parent]

        # Parse the VI hierarchy
        self._load_vi_recursive(
            bd_xml,
            main_xml,
            expand_subvis=expand_subvis,
            search_paths=search_paths,
            visited=set(),
        )

    def _load_vi_recursive(
        self,
        bd_xml: Path,
        main_xml: Path | None,
        expand_subvis: bool,
        search_paths: list[Path],
        visited: set[str],
    ) -> str | None:
        """Recursively load a VI and its SubVIs.

        Returns the VI name or None if already visited.
        """
        # Determine VI name from path
        vi_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        if vi_name in visited:
            return None
        visited.add(vi_name)

        # Parse the block diagram
        bd = parse_block_diagram(bd_xml)

        # Build dataflow graph for this VI
        self._dataflow[vi_name] = self._build_dataflow_graph(bd, vi_name)

        # Add to dependency graph
        self._dep_graph.add_node(vi_name)

        # Process SubVIs using parse_subvi_paths (needs main XML, not BDHb)
        if expand_subvis and main_xml and main_xml.exists():
            subvi_refs = parse_subvi_paths(main_xml)
            for ref in subvi_refs:
                subvi_path = self._find_subvi(ref.name, search_paths)
                if subvi_path:
                    # Recursively load SubVI
                    subvi_bd_xml, _, subvi_main_xml = extract_vi_xml(subvi_path)
                    loaded_name = self._load_vi_recursive(
                        subvi_bd_xml,
                        subvi_main_xml,
                        expand_subvis=True,
                        search_paths=search_paths,
                        visited=visited,
                    )
                    if loaded_name:
                        self._dep_graph.add_edge(vi_name, loaded_name)
                else:
                    # Mark as stub
                    self._stubs.add(ref.name)
                    self._dep_graph.add_node(ref.name)
                    self._dep_graph.add_edge(vi_name, ref.name)

        return vi_name

    def _find_subvi(self, vi_path: str, search_paths: list[Path]) -> Path | None:
        """Find a SubVI file in search paths."""
        vi_name = Path(vi_path).name
        for search_path in search_paths:
            # Direct path
            candidate = search_path / vi_name
            if candidate.exists():
                return candidate
            # Recursive search
            for found in search_path.rglob(vi_name):
                return found
        return None

    def _build_dataflow_graph(self, bd: BlockDiagram, vi_name: str) -> nx.DiGraph:
        """Build a dataflow graph from a BlockDiagram.

        Nodes: operations, constants, FP terminals (inputs/outputs)
        Edges: wires (data connections)
        """
        g = nx.DiGraph()

        # Add FP terminals (inputs/outputs)
        for fp_term in bd.fp_terminals:
            kind = "output" if fp_term.is_indicator else "input"
            g.add_node(
                fp_term.uid,
                kind=kind,
                name=fp_term.name,
                is_indicator=fp_term.is_indicator,
            )

        # Add constants
        for const in bd.constants:
            g.add_node(
                const.uid,
                kind="constant",
                value=const.value,
                type=const.type_desc,
                label=const.label,
            )

        # Add operations (SubVIs and primitives)
        for node in bd.nodes:
            if node.node_type == "iUse":
                node_kind = "subvi"
            elif node.node_type == "prim" or node.prim_index:
                node_kind = "primitive"
            else:
                node_kind = "operation"

            # Collect terminals for this operation
            terminals = []
            for term_uid, term_info in bd.terminal_info.items():
                if term_info.parent_uid == node.uid:
                    terminals.append({
                        "id": term_uid,
                        "index": term_info.index,
                        "type": term_info.type_id or "unknown",
                        "name": term_info.name,
                        "direction": "output" if term_info.is_output else "input",
                    })

            g.add_node(
                node.uid,
                kind=node_kind,
                name=node.name,
                prim_id=node.prim_res_id,
                prim_index=node.prim_index,
                node_type=node.node_type,
                terminals=sorted(terminals, key=lambda t: t.get("index", 0)),
            )

        # Add terminal nodes (for wire routing)
        for term_uid, term_info in bd.terminal_info.items():
            if term_uid not in g:  # Don't override FP terminals
                g.add_node(
                    term_uid,
                    kind="terminal",
                    parent_id=term_info.parent_uid,
                    index=term_info.index,
                    type=term_info.type_id or "unknown",
                    name=term_info.name,
                    direction="output" if term_info.is_output else "input",
                )

        # Add edges (wires)
        for wire in bd.wires:
            from_parent = None
            to_parent = None
            if wire.from_term in bd.terminal_info:
                from_parent = bd.terminal_info[wire.from_term].parent_uid
            if wire.to_term in bd.terminal_info:
                to_parent = bd.terminal_info[wire.to_term].parent_uid

            g.add_edge(
                wire.from_term,
                wire.to_term,
                from_parent=from_parent,
                to_parent=to_parent,
            )

        return g

    # === Dependency Graph Queries ===

    def list_vis(self) -> list[str]:
        """List all VIs in the graph (excluding stubs)."""
        return list(self._dataflow.keys())

    def is_stub_vi(self, vi_name: str) -> bool:
        """Check if a VI is a stub (missing dependency)."""
        return vi_name in self._stubs

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
        return [n for n in self._dep_graph.nodes() if self._dep_graph.out_degree(n) == 0]

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

        # Condense SCCs into single nodes
        condensation = nx.condensation(self._dep_graph)

        # Topologically sort the condensation (SCCs in order)
        # Reverse because we want dependencies first
        scc_order = list(reversed(list(nx.topological_sort(condensation))))

        for scc_id in scc_order:
            members = condensation.nodes[scc_id]["members"]
            # Filter out stubs
            real_vis = {m for m in members if m not in self._stubs}
            if real_vis:
                yield real_vis

    def get_conversion_order(self) -> list[str]:
        """Get VIs in topological order for bottom-up conversion.

        Returns flat list. For recursive VIs, order within SCC is arbitrary.
        Use get_generation_order() for proper grouping.
        """
        result = []
        for group in self.get_generation_order():
            result.extend(sorted(group))  # Sort for determinism
        return result

    # === Dataflow Graph Queries ===

    def get_dataflow_graph(self, vi_name: str) -> nx.DiGraph | None:
        """Get the raw dataflow graph for a VI."""
        return self._dataflow.get(vi_name)

    def get_node(self, vi_name: str, node_id: str) -> dict[str, Any] | None:
        """Get a node's attributes from a VI's dataflow graph."""
        g = self._dataflow.get(vi_name)
        if g is None or node_id not in g:
            return None
        return dict(g.nodes[node_id])

    def get_inputs(self, vi_name: str) -> list[dict[str, Any]]:
        """Get VI input terminals."""
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        return [
            {"id": n, **d}
            for n, d in g.nodes(data=True)
            if d.get("kind") == "input"
        ]

    def get_outputs(self, vi_name: str) -> list[dict[str, Any]]:
        """Get VI output terminals."""
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        return [
            {"id": n, **d}
            for n, d in g.nodes(data=True)
            if d.get("kind") == "output"
        ]

    def get_constants(self, vi_name: str) -> list[dict[str, Any]]:
        """Get all constants in a VI."""
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        return [
            {"id": n, **d}
            for n, d in g.nodes(data=True)
            if d.get("kind") == "constant"
        ]

    def get_operations(self, vi_name: str) -> list[dict[str, Any]]:
        """Get all operations (SubVIs, primitives) in a VI.

        Returns operations in dataflow execution order with backward-compatible format.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []

        # Get operations in dataflow order
        ordered_ids = self.get_operation_order(vi_name)
        op_set = set(ordered_ids)

        # Add any ops not in the sorted order (disconnected)
        for n, d in g.nodes(data=True):
            if d.get("kind") in ("subvi", "primitive", "operation") and n not in op_set:
                ordered_ids.append(n)

        result = []
        for n in ordered_ids:
            d = dict(g.nodes[n])
            kind = d.get("kind", "operation")

            # Convert kind to labels for backward compatibility
            if kind == "subvi":
                labels = ["SubVI"]
            elif kind == "primitive":
                labels = ["Primitive"]
            else:
                labels = ["Operation"]

            result.append({
                "id": n,
                "name": d.get("name"),
                "labels": labels,
                "primResID": d.get("prim_id"),
                "terminals": d.get("terminals", []),
            })
        return result

    def get_operation_order(self, vi_name: str) -> list[str]:
        """Get operations in dataflow execution order.

        Returns operation node IDs in the order they should execute
        (topological sort based on wire connections).
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []

        # Get operation node IDs
        op_ids = set(
            n for n, d in g.nodes(data=True)
            if d.get("kind") in ("subvi", "primitive", "operation")
        )

        if not op_ids:
            return []

        # Build a map from terminal ID to parent operation ID
        terminal_to_op: dict[str, str] = {}
        for n, d in g.nodes(data=True):
            if d.get("kind") == "terminal":
                parent = d.get("parent_id")
                if parent:
                    terminal_to_op[n] = parent

        # Build operation-level dependency graph
        # Operation A depends on B if any wire goes from B's output terminal to A's input terminal
        op_deps = nx.DiGraph()
        op_deps.add_nodes_from(op_ids)

        for u, v, _ in g.edges(data=True):
            # u is source terminal, v is destination terminal
            src_op = terminal_to_op.get(u)
            dst_op = terminal_to_op.get(v)

            # Add edge if both terminals belong to operations (not constants/FP terminals)
            if src_op in op_ids and dst_op in op_ids and src_op != dst_op:
                op_deps.add_edge(src_op, dst_op)

        try:
            return list(nx.topological_sort(op_deps))
        except nx.NetworkXUnfeasible:
            # Cycle in operations (shouldn't happen in valid VI)
            return list(op_ids)

    def get_predecessors(self, vi_name: str, node_id: str) -> list[str]:
        """Get nodes that feed into this node (direct predecessors)."""
        g = self._dataflow.get(vi_name)
        if g is None or node_id not in g:
            return []
        return list(g.predecessors(node_id))

    def get_successors(self, vi_name: str, node_id: str) -> list[str]:
        """Get nodes that this node feeds into (direct successors)."""
        g = self._dataflow.get(vi_name)
        if g is None or node_id not in g:
            return []
        return list(g.successors(node_id))

    def get_source_of_output(self, vi_name: str, output_id: str) -> str | None:
        """Trace an output terminal back to its source node.

        Returns the ID of the node that produces the value for this output.
        """
        g = self._dataflow.get(vi_name)
        if g is None or output_id not in g:
            return None

        preds = list(g.predecessors(output_id))
        if not preds:
            return None

        # Follow through terminals to find the actual source
        source = preds[0]
        source_data = g.nodes.get(source, {})

        # If it's a terminal, get its parent
        if source_data.get("kind") == "terminal":
            return source_data.get("parent_id")

        return source

    def get_wires(self, vi_name: str) -> list[dict[str, Any]]:
        """Get all wires (edges) in a VI's dataflow graph.

        Returns wires in backward-compatible format for skeleton generator.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        return [
            {
                "from_terminal_id": u,
                "to_terminal_id": v,
                "from_parent_id": d.get("from_parent"),
                "to_parent_id": d.get("to_parent"),
            }
            for u, v, d in g.edges(data=True)
        ]

    # === Legacy API (for backward compatibility) ===

    def get_vi_context(self, vi_name: str) -> dict[str, Any]:
        """Get complete VI context for code generation.

        Returns a dict with inputs, outputs, constants, operations, etc.
        This is for backward compatibility with the old dict-based API.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return {}

        # Build subvi_calls list
        subvi_calls = []
        for n, d in g.nodes(data=True):
            if d.get("kind") == "subvi":
                subvi_calls.append({
                    "call_name": d.get("name"),
                    "vi_name": d.get("name"),
                })

        # Build terminals list for skeleton generator
        terminals = []
        for n, d in g.nodes(data=True):
            if d.get("kind") == "terminal":
                terminals.append({
                    "id": n,
                    "parent_id": d.get("parent_id"),
                    "index": d.get("index"),
                    "type": d.get("type"),
                    "name": d.get("name"),
                    "direction": d.get("direction"),
                })

        return {
            "name": vi_name,
            "inputs": self.get_inputs(vi_name),
            "outputs": self.get_outputs(vi_name),
            "constants": self.get_constants(vi_name),
            "operations": self.get_operations(vi_name),
            "terminals": terminals,
            "data_flow": self.get_wires(vi_name),
            "subvi_calls": subvi_calls,
        }

    def get_subvi_calls(self, vi_name: str) -> list[dict]:
        """Get SubVIs called by a VI."""
        ctx = self.get_vi_context(vi_name)
        return ctx.get("subvi_calls", [])

    # === Context Manager ===

    def __enter__(self) -> InMemoryVIGraph:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.clear()


def connect() -> InMemoryVIGraph:
    """Create an in-memory VI graph (no connection needed)."""
    return InMemoryVIGraph()

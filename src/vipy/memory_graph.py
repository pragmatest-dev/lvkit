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

from .blockdiagram import decode_constant
from .extractor import extract_vi_xml
from .frontpanel import FrontPanel, parse_front_panel
from .parser import (
    BlockDiagram,
    ConnectorPane,
    parse_block_diagram,
    parse_connector_pane,
    parse_connector_pane_types,
    parse_subvi_paths,
)
from .types import from_labview_type
from .vilib_resolver import get_resolver as get_vilib_resolver


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
        # Cross-VI bindings: (caller_vi, term_uid) -> (subvi_name, subvi_term_uid)
        self._bindings: dict[tuple[str, str], tuple[str, str]] = {}
        # Loop structures: vi_name -> {loop_uid -> LoopStructure}
        self._loop_structures: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        """Clear all loaded data."""
        self._dep_graph.clear()
        self._dataflow.clear()
        self._stubs.clear()
        self._bindings.clear()
        self._loop_structures.clear()

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Cypher query compatibility - routes to native methods.

        Detects query intent and calls appropriate native method.
        """
        cypher_lower = cypher.lower()

        # Route to native methods based on query pattern
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
        for vi_name, g in self._dataflow.items():
            for node_id, data in g.nodes(data=True):
                if data.get("kind") == "constant":
                    results.append({
                        "vi_name": vi_name,
                        "value": data.get("raw_value", data.get("value", "")),
                        "label": data.get("label"),
                        "type": data.get("type"),
                        "python": data.get("value"),  # Decoded value
                    })
        return results

    def get_all_primitives(self) -> list[dict[str, Any]]:
        """Get all primitives across all VIs for primitive discovery."""
        results = []
        for vi_name, g in self._dataflow.items():
            for node_id, data in g.nodes(data=True):
                if data.get("kind") == "primitive":
                    terminals = data.get("terminals", [])
                    input_types = [
                        t.get("type", "Any")
                        for t in terminals
                        if t.get("direction") == "input"
                    ]
                    output_types = [
                        t.get("type", "Any")
                        for t in terminals
                        if t.get("direction") == "output"
                    ]
                    results.append({
                        "vi_name": vi_name,
                        "prim_id": data.get("prim_id"),
                        "input_types": input_types,
                        "output_types": output_types,
                    })
        return results

    def get_all_clusters(self) -> list[dict[str, Any]]:
        """Get all cluster types across all VIs for shared type discovery."""
        # Collect clusters by name, tracking which VIs use them
        clusters: dict[str, set[str]] = {}

        for vi_name, g in self._dataflow.items():
            for node_id, data in g.nodes(data=True):
                if data.get("kind") in ("input", "output"):
                    control_type = data.get("control_type", "")
                    if control_type == "stdClust":
                        name = data.get("name", "UnnamedCluster")
                        if name not in clusters:
                            clusters[name] = set()
                        clusters[name].add(vi_name)

        return [
            {"name": name, "id": name, "vis": list(vis)}
            for name, vis in clusters.items()
        ]

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
            bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)
        elif vi_path.name.endswith("_BDHb.xml"):
            bd_xml = vi_path
            # Try to find FP XML and main XML
            fp_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", "_FPHb.xml"))
            if not fp_xml.exists():
                fp_xml = None
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
            fp_xml,
            main_xml,
            expand_subvis=expand_subvis,
            search_paths=search_paths,
            visited=set(),
        )

        # Build cross-VI bindings after all VIs are loaded
        if expand_subvis:
            self._build_cross_vi_bindings()

    def _load_vi_recursive(
        self,
        bd_xml: Path,
        fp_xml: Path | None,
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

        # Parse block diagram, front panel, and connector pane
        bd = parse_block_diagram(bd_xml)
        fp: FrontPanel | None = None
        conpane: ConnectorPane | None = None
        wiring_rules: dict[int, int] = {}
        if fp_xml and fp_xml.exists():
            fp = parse_front_panel(fp_xml, bd_xml)
            conpane = parse_connector_pane(fp_xml)
            # Parse wiring rules from main XML if available
            if main_xml and main_xml.exists() and conpane:
                wiring_rules = parse_connector_pane_types(main_xml, conpane)

        # Build dataflow graph for this VI
        self._dataflow[vi_name] = self._build_dataflow_graph(
            bd, fp, conpane, wiring_rules, vi_name
        )

        # Store loop structures for later lookup
        self._loop_structures[vi_name] = {loop.uid: loop for loop in bd.loops}

        # Add to dependency graph
        self._dep_graph.add_node(vi_name)

        # Process SubVIs - only those actually CALLED in the block diagram
        # Both iUse and polyIUse are SubVI calls
        if expand_subvis and main_xml and main_xml.exists():
            # Get names of SubVIs actually called (iUse and polyIUse nodes)
            called_subvis = {
                node.name for node in bd.nodes
                if node.node_type in ("iUse", "polyIUse") and node.name
            }

            # Get path hints from main XML for resolving locations
            subvi_refs = parse_subvi_paths(main_xml)
            subvi_ref_map = {ref.name: ref for ref in subvi_refs}

            for subvi_name in called_subvis:
                ref = subvi_ref_map.get(subvi_name)
                # Use ref.name if available, otherwise use the raw subvi_name
                lookup_name = ref.name if ref else subvi_name
                subvi_path = self._find_subvi(lookup_name, search_paths)
                if subvi_path:
                    # Recursively load SubVI
                    try:
                        subvi_bd_xml, subvi_fp_xml, subvi_main_xml = extract_vi_xml(
                            subvi_path
                        )
                        loaded_name = self._load_vi_recursive(
                            subvi_bd_xml,
                            subvi_fp_xml,
                            subvi_main_xml,
                            expand_subvis=True,
                            search_paths=search_paths,
                            visited=visited,
                        )
                        if loaded_name:
                            self._dep_graph.add_edge(vi_name, loaded_name)
                    except (RuntimeError, OSError):
                        # SubVI extraction failed (corrupt file) - treat as stub
                        self._stubs.add(subvi_name)
                        self._dep_graph.add_node(subvi_name)
                        self._dep_graph.add_edge(vi_name, subvi_name)
                else:
                    # Mark as stub
                    self._stubs.add(subvi_name)
                    self._dep_graph.add_node(subvi_name)
                    self._dep_graph.add_edge(vi_name, subvi_name)

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

    def _build_cross_vi_bindings(self) -> None:
        """Build explicit bindings between caller terminals and SubVI parameters.

        For each SubVI call, maps caller terminal[index=N] to SubVI FP terminal[slot=N].
        This makes cross-VI data flow explicit in the graph.
        """
        for caller_vi in self._dataflow:
            g = self._dataflow[caller_vi]

            # Find SubVI nodes in this VI
            for node_id, data in g.nodes(data=True):
                if data.get("kind") != "subvi":
                    continue

                subvi_name = data.get("name")
                if not subvi_name or subvi_name not in self._dataflow:
                    continue  # SubVI not loaded (stub)

                # Get SubVI's FP terminals indexed by slot
                subvi_g = self._dataflow[subvi_name]
                slot_to_term: dict[int, str] = {}
                for term_id, term_data in subvi_g.nodes(data=True):
                    slot = term_data.get("slot_index")
                    if slot is not None and term_data.get("kind") in (
                        "input",
                        "output",
                    ):
                        slot_to_term[slot] = term_id

                # Get terminals on the SubVI node in caller
                terminals = data.get("terminals", [])
                for term in terminals:
                    term_uid = term.get("id")
                    term_index = term.get("index")
                    if term_uid is None or term_index is None:
                        continue

                    # Match caller terminal index to SubVI slot
                    subvi_term_uid = slot_to_term.get(term_index)
                    if subvi_term_uid:
                        self._bindings[(caller_vi, term_uid)] = (
                            subvi_name,
                            subvi_term_uid,
                        )

    def _build_dataflow_graph(
        self,
        bd: BlockDiagram,
        fp: FrontPanel | None,
        conpane: ConnectorPane | None,
        wiring_rules: dict[int, int],
        vi_name: str,
    ) -> nx.DiGraph:
        """Build a dataflow graph from a BlockDiagram.

        Nodes: operations, constants, FP terminals (inputs/outputs)
        Edges: wires (data connections)

        Only includes FP terminals that are on the connector pane (public interface).
        """
        g = nx.DiGraph()

        # Build FP control lookup by UID for merging names/types/defaults
        fp_by_uid: dict[str, Any] = {}
        if fp:
            for ctrl in fp.controls:
                fp_by_uid[ctrl.uid] = ctrl

        # Build connector pane lookup: fp_dco_uid -> slot index
        conpane_slots: dict[str, int] = {}
        if conpane:
            for slot in conpane.slots:
                if slot.fp_dco_uid:
                    conpane_slots[slot.fp_dco_uid] = slot.index

        # Add ALL FP terminals (for dataflow), marking public vs internal
        for fp_term in bd.fp_terminals:
            slot_index = conpane_slots.get(fp_term.fp_dco_uid)
            is_public = slot_index is not None or not conpane_slots
            kind = "output" if fp_term.is_indicator else "input"
            # Look up front panel control by DCO UID
            ctrl = fp_by_uid.get(fp_term.fp_dco_uid)
            # Get wiring rule for this slot (default to 0 = Invalid/optional)
            wiring_rule = wiring_rules.get(slot_index, 0) if slot_index else 0
            g.add_node(
                fp_term.uid,
                kind=kind,
                name=ctrl.name if ctrl else fp_term.name,
                is_indicator=fp_term.is_indicator,
                is_public=is_public,  # On connector pane = public interface
                slot_index=slot_index,  # Position on connector pane
                wiring_rule=wiring_rule,  # 0=Invalid, 1=Required, 2=Recommended, 3=Optional
                type_desc=ctrl.type_desc if ctrl else None,
                control_type=ctrl.control_type if ctrl else None,
                default_value=ctrl.default_value if ctrl else None,
                enum_values=ctrl.enum_values if ctrl else [],
            )

        # Add constants WITH DECODED VALUES
        for const in bd.constants:
            val_type, decoded_value = decode_constant(const)
            g.add_node(
                const.uid,
                kind="constant",
                value=decoded_value,
                type=val_type,
                raw_value=const.value,
                label=const.label,
            )

        # Add operations (SubVIs and primitives)
        for node in bd.nodes:
            if node.node_type in ("iUse", "polyIUse"):
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

        # Add tunnel edges from loop structures
        # These create implicit data flow through loop boundaries
        for loop in bd.loops:
            for tunnel in loop.tunnels:
                # Get parent info for tunnel terminals
                outer_parent = bd.terminal_info.get(tunnel.outer_terminal_uid)
                inner_parent = bd.terminal_info.get(tunnel.inner_terminal_uid)
                outer_parent_id = outer_parent.parent_uid if outer_parent else loop.uid
                inner_parent_id = inner_parent.parent_uid if inner_parent else loop.uid

                # lSR (left shift register) and lpTun: data flows INTO the loop
                # outer -> inner
                if tunnel.tunnel_type in ("lSR", "lpTun"):
                    g.add_edge(
                        tunnel.outer_terminal_uid,
                        tunnel.inner_terminal_uid,
                        tunnel_type=tunnel.tunnel_type,
                        loop_uid=loop.uid,
                        from_parent=outer_parent_id,
                        to_parent=inner_parent_id,
                    )
                # rSR (right shift register) and lMax: data flows OUT of the loop
                # inner -> outer
                elif tunnel.tunnel_type in ("rSR", "lMax"):
                    g.add_edge(
                        tunnel.inner_terminal_uid,
                        tunnel.outer_terminal_uid,
                        tunnel_type=tunnel.tunnel_type,
                        loop_uid=loop.uid,
                        from_parent=inner_parent_id,
                        to_parent=outer_parent_id,
                    )

        return g

    # === Dependency Graph Queries ===

    def list_vis(self) -> list[str]:
        """List all VIs in the graph (excluding stubs)."""
        return list(self._dataflow.keys())

    def is_stub_vi(self, vi_name: str) -> bool:
        """Check if a VI is a stub (missing dependency)."""
        return vi_name in self._stubs

    def get_stub_vi_info(self, vi_name: str) -> dict[str, Any] | None:
        """Get stub VI info from vilib reference or call site inference.

        Priority:
        1. Check vilib resolver for known VI signatures (vi.lib VIs)
        2. Fall back to inferring from call site in parent VI
        """
        if vi_name not in self._stubs:
            return None

        # First, check vilib resolver for known VIs
        from .vilib_resolver import get_resolver
        resolver = get_resolver()
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

        for caller_vi in self._dataflow:
            g = self._dataflow[caller_vi]
            for node_id, data in g.nodes(data=True):
                if data.get("kind") == "subvi" and data.get("name") == vi_name:
                    # Extract terminal types from the SubVI node
                    for term in data.get("terminals", []):
                        term_type = term.get("type", "Any")
                        if term_type == "unknown":
                            term_type = "Any"
                        if term.get("direction") == "input":
                            input_types.append(term_type)
                        else:
                            output_types.append(term_type)
                    break  # Found caller, stop searching

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

        # Condense SCCs into single nodes
        condensation = nx.condensation(self._dep_graph)

        # Topologically sort the condensation (SCCs in order)
        # Reverse because we want dependencies first
        scc_order = list(reversed(list(nx.topological_sort(condensation))))

        # vilib VIs have implementations, so include them in conversion order
        vilib_resolver = get_vilib_resolver()

        for scc_id in scc_order:
            members = condensation.nodes[scc_id]["members"]
            # Include real VIs and stubs that have vilib implementations
            convertible_vis = {
                m for m in members
                if m not in self._stubs or vilib_resolver.has_implementation(m)
            }
            if convertible_vis:
                yield convertible_vis

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

    def get_inputs(
        self, vi_name: str, *, public_only: bool = True
    ) -> list[dict[str, Any]]:
        """Get VI input terminals.

        Args:
            vi_name: Name of the VI
            public_only: If True, only return connector pane inputs (default).
                        If False, include internal controls too.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        return [
            {"id": n, **d}
            for n, d in g.nodes(data=True)
            if d.get("kind") == "input"
            and (not public_only or d.get("is_public", True))
        ]

    def get_outputs(
        self, vi_name: str, *, public_only: bool = True
    ) -> list[dict[str, Any]]:
        """Get VI output terminals.

        Args:
            vi_name: Name of the VI
            public_only: If True, only return connector pane outputs (default).
                        If False, include internal indicators too.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        return [
            {"id": n, **d}
            for n, d in g.nodes(data=True)
            if d.get("kind") == "output"
            and (not public_only or d.get("is_public", True))
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
            node_type = d.get("node_type", "")

            # Convert kind to labels for backward compatibility
            if kind == "subvi":
                labels = ["SubVI"]
            elif kind == "primitive":
                labels = ["Primitive"]
            else:
                labels = ["Operation"]

            op_dict: dict[str, Any] = {
                "id": n,
                "name": d.get("name"),
                "labels": labels,
                "primResID": d.get("prim_id"),
                "terminals": d.get("terminals", []),
            }

            # Add loop_type for loop structures
            if node_type in ("whileLoop", "forLoop"):
                op_dict["loop_type"] = node_type
                op_dict["labels"] = ["Loop"]  # Override label for loops

                # Get LoopStructure data
                loop_struct = self._loop_structures.get(vi_name, {}).get(n)
                if loop_struct:
                    op_dict["tunnels"] = [
                        {
                            "outer_terminal_uid": t.outer_terminal_uid,
                            "inner_terminal_uid": t.inner_terminal_uid,
                            "tunnel_type": t.tunnel_type,
                            "paired_terminal_uid": t.paired_terminal_uid,
                        }
                        for t in loop_struct.tunnels
                    ]
                    op_dict["inner_nodes"] = self._build_inner_nodes(
                        loop_struct.inner_node_uids, g, vi_name
                    )

            result.append(op_dict)
        return result

    def _build_inner_nodes(
        self, uids: list[str], g: nx.DiGraph, vi_name: str
    ) -> list[dict[str, Any]]:
        """Build operation dicts for nodes inside a loop.

        Recursively handles nested loops.
        """
        inner_ops = []
        for uid in uids:
            if uid not in g.nodes:
                continue
            d = dict(g.nodes[uid])
            kind = d.get("kind", "operation")
            node_type = d.get("node_type", "")

            # Same labeling logic as get_operations
            if kind == "subvi":
                labels = ["SubVI"]
            elif kind == "primitive":
                labels = ["Primitive"]
            else:
                labels = ["Operation"]

            inner_op: dict[str, Any] = {
                "id": uid,
                "name": d.get("name"),
                "labels": labels,
                "primResID": d.get("prim_id"),
                "terminals": d.get("terminals", []),
            }

            # Handle nested loops recursively
            if node_type in ("whileLoop", "forLoop"):
                inner_op["loop_type"] = node_type
                inner_op["labels"] = ["Loop"]
                nested_struct = self._loop_structures.get(vi_name, {}).get(uid)
                if nested_struct:
                    inner_op["tunnels"] = [
                        {
                            "outer_terminal_uid": t.outer_terminal_uid,
                            "inner_terminal_uid": t.inner_terminal_uid,
                            "tunnel_type": t.tunnel_type,
                            "paired_terminal_uid": t.paired_terminal_uid,
                        }
                        for t in nested_struct.tunnels
                    ]
                    inner_op["inner_nodes"] = self._build_inner_nodes(
                        nested_struct.inner_node_uids, g, vi_name
                    )

            inner_ops.append(inner_op)
        return inner_ops

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
        # Operation A depends on B if a wire goes from B's output to A's input
        op_deps = nx.DiGraph()
        op_deps.add_nodes_from(op_ids)

        for u, v, _ in g.edges(data=True):
            # u is source terminal, v is destination terminal
            src_op = terminal_to_op.get(u)
            dst_op = terminal_to_op.get(v)

            # Add edge if both terminals belong to operations (not FP/constants)
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

        result = []
        for u, v, d in g.edges(data=True):
            from_parent_id = d.get("from_parent")
            to_parent_id = d.get("to_parent")

            # Look up parent node data for labels and names
            from_node = g.nodes.get(from_parent_id, {}) if from_parent_id else {}
            to_node = g.nodes.get(to_parent_id, {}) if to_parent_id else {}

            # Get labels from node kind
            from_kind = from_node.get("kind", "")
            to_kind = to_node.get("kind", "")
            from_labels = self._kind_to_labels(from_kind)
            to_labels = self._kind_to_labels(to_kind)

            result.append({
                "from_terminal_id": u,
                "to_terminal_id": v,
                "from_parent_id": from_parent_id,
                "to_parent_id": to_parent_id,
                "from_parent_name": from_node.get("name"),
                "to_parent_name": to_node.get("name"),
                "from_parent_labels": from_labels,
                "to_parent_labels": to_labels,
            })
        return result

    def _kind_to_labels(self, kind: str) -> list[str]:
        """Convert internal kind to labels list."""
        if kind == "subvi":
            return ["SubVI"]
        elif kind == "primitive":
            return ["Primitive"]
        elif kind == "operation":
            return ["Operation"]
        elif kind == "constant":
            return ["Constant"]
        elif kind == "input":
            return ["Control", "Input"]
        elif kind == "output":
            return ["Indicator", "Output"]
        return []

    # === Cross-VI Bindings ===

    def get_binding(
        self, caller_vi: str, caller_term_uid: str
    ) -> tuple[str, str] | None:
        """Get the SubVI terminal that a caller terminal binds to.

        Args:
            caller_vi: Name of the calling VI
            caller_term_uid: UID of the terminal on the SubVI node in caller

        Returns:
            Tuple of (subvi_name, subvi_term_uid) or None if not bound
        """
        return self._bindings.get((caller_vi, caller_term_uid))

    def get_bindings_for_vi(self, vi_name: str) -> list[dict[str, Any]]:
        """Get all cross-VI bindings where this VI is the caller.

        Returns list of binding dicts with caller and target info.
        """
        result = []
        for (caller, term_uid), (subvi, subvi_term) in self._bindings.items():
            if caller == vi_name:
                result.append({
                    "caller_vi": caller,
                    "caller_term": term_uid,
                    "subvi_name": subvi,
                    "subvi_term": subvi_term,
                })
        return result

    def trace_data_flow(
        self, vi_name: str, term_uid: str, *, cross_vi: bool = True
    ) -> list[dict[str, Any]]:
        """Trace data flow from a terminal, optionally crossing VI boundaries.

        Args:
            vi_name: Starting VI
            term_uid: Starting terminal UID
            cross_vi: If True, follow bindings into SubVIs

        Returns:
            List of nodes in data flow order, with VI context
        """
        result = []
        visited = set()

        def trace(vi: str, uid: str) -> None:
            if (vi, uid) in visited:
                return
            visited.add((vi, uid))

            g = self._dataflow.get(vi)
            if g is None or uid not in g:
                return

            node_data = dict(g.nodes[uid])
            result.append({"vi": vi, "id": uid, **node_data})

            # Follow outgoing edges (downstream data flow)
            for succ in g.successors(uid):
                trace(vi, succ)

            # If this is a SubVI node terminal and cross_vi is enabled,
            # follow binding into the SubVI
            if cross_vi:
                binding = self._bindings.get((vi, uid))
                if binding:
                    subvi_name, subvi_term = binding
                    trace(subvi_name, subvi_term)

        trace(vi_name, term_uid)
        return result

    # === Legacy API (for backward compatibility) ===

    def get_vi_context(self, vi_name: str) -> dict[str, Any]:
        """Get complete VI context for code generation.

        Returns a dict with inputs, outputs, constants, operations, etc.
        Includes TypeInfo objects for structured type handling.
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

        # Get inputs/outputs with TypeInfo enrichment
        inputs = self._enrich_with_type_info(self.get_inputs(vi_name))
        outputs = self._enrich_with_type_info(self.get_outputs(vi_name))
        constants = self._enrich_with_type_info(self.get_constants(vi_name))

        return {
            "name": vi_name,
            "inputs": inputs,
            "outputs": outputs,
            "constants": constants,
            "operations": self.get_operations(vi_name),
            "terminals": terminals,
            "data_flow": self.get_wires(vi_name),
            "subvi_calls": subvi_calls,
        }

    def _enrich_with_type_info(self, items: list[dict]) -> list[dict]:
        """Add TypeInfo objects to context items.

        Adds 'type_info' field based on 'type' and 'control_type' fields.
        """
        for item in items:
            lv_type = item.get("type", "")
            control_type = item.get("control_type", "")
            item["type_info"] = from_labview_type(lv_type, control_type)
        return items

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

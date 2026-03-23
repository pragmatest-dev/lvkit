"""In-memory graph using NetworkX — unified architecture.

Single unified nx.MultiDiGraph holds all VIs. Each graph node stores a typed
Pydantic model (VINode, PrimitiveNode, StructureNode, ConstantNode) as
``graph.nodes[uid]["node"]``. Edges store typed WireEnd source/dest objects.

No external lookup maps — all data lives ON the graph.
No terminal-only nodes — terminals are lists on their parent nodes.
No sRN infrastructure nodes — sRN connections stored on StructureNode.

Dependency ordering uses a separate nx.DiGraph (_dep_graph).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import networkx as nx

from .blockdiagram import decode_constant
from .extractor import extract_vi_xml
from .graph_types import (
    AnyGraphNode,
    BranchPoint,
    CaseFrame,
    Constant,
    ConstantNode,
    FPTerminal,
    FrameInfo,
    LVType,
    Operation,
    ParallelBranch,
    PrimitiveNode as GraphPrimitiveNode,
    PropertyDef,
    StructureNode,
    Terminal,
    Tunnel,
    TunnelTerminal,
    VINode,
    Wire,
    WireEnd,
    control_type_to_lvtype,
)
from .parser import (
    BlockDiagram,
    ConnectorPane,
    FrontPanel,
    parse_connector_pane_types,
    parse_vi,
    parse_vi_metadata,
)
from .parser.models import ParsedType
from .parser.node_types import (
    CpdArithNode,
    InvokeNode,
    PrimitiveNode as ParserPrimitiveNode,
    PropertyNode,
    SubVINode,
)
from .primitive_resolver import get_resolver as get_prim_resolver
from .primitive_resolver import resolve_primitive
from .structure import (
    get_project_classes,
    get_project_libraries,
    get_project_vis,
    parse_lvclass,
    parse_lvlib,
    parse_lvproj,
)
from .vilib_resolver import get_resolver as get_vilib_resolver

# Map node types to human-readable names for nodes without explicit names
_NODE_TYPE_NAMES: dict[str, str] = {
    "whileLoop": "While Loop",
    "forLoop": "For Loop",
    "caseStruct": "Case Structure",
    "seqFrame": "Sequence Frame",
    "eventStruct": "Event Structure",
    "flatSequence": "Flat Sequence",
    "seq": "Stacked Sequence",
}

# Map operation kind to labels
_KIND_TO_LABELS: dict[str, list[str]] = {
    "vi": ["SubVI"],
    "primitive": ["Primitive"],
    "caseStruct": ["CaseStructure"],
    "loop": ["Loop"],
}

# Graph node kinds that represent executable operations
_OPERATION_KINDS = ("vi", "primitive", "operation", "caseStruct", "loop")

# Graph node kind literals used by typed graph nodes
_GRAPH_NODE_KIND_MAP = {
    "vi": "vi",
    "primitive": "primitive",
    "structure": "structure",
    "constant": "constant",
}


def _get_operation_labels(kind: str) -> list[str]:
    """Get labels for an operation based on its kind."""
    return _KIND_TO_LABELS.get(kind, ["Operation"])


def _graph_node_to_op_kind(node: AnyGraphNode) -> str:
    """Map a typed graph node to the operation kind string."""
    if isinstance(node, VINode):
        return "vi"
    if isinstance(node, GraphPrimitiveNode):
        if node.node_type in ("caseStruct", "select"):
            return "caseStruct"
        if node.node_type in ("whileLoop", "forLoop"):
            return "loop"
        return "primitive"
    if isinstance(node, StructureNode):
        if node.node_type in ("caseStruct", "select"):
            return "caseStruct"
        if node.node_type in ("whileLoop", "forLoop"):
            return "loop"
        return "operation"
    if isinstance(node, ConstantNode):
        return "constant"
    return "operation"


class InMemoryVIGraph:
    """In-memory VI graph using a single unified NetworkX MultiDiGraph.

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

    def __init__(self) -> None:
        # Unified graph: all VIs, all nodes, all edges
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        # Per-VI node index: vi_name -> set of node UIDs in that VI
        self._vi_nodes: dict[str, set[str]] = {}
        # Terminal ownership: terminal_id -> node_id
        # Enables O(1) node lookup for incoming_edges/outgoing_edges
        self._term_to_node: dict[str, str] = {}
        # Dependency graph: VI name -> VI name (caller -> callee)
        self._dep_graph: nx.DiGraph = nx.DiGraph()
        # Stub VIs (missing dependencies)
        self._stubs: set[str] = set()
        # Polymorphic VI info: vi_name -> {is_polymorphic, variants}
        self._poly_info: dict[str, dict[str, Any]] = {}
        # Qualified name aliases: "Lib.lvlib:VI.vi" -> "VI.vi" (for library VIs)
        self._qualified_aliases: dict[str, str] = {}
        # Track loaded VIs across multiple load_vi() calls to prevent re-parsing
        self._loaded_vis: set[str] = set()
        # Source file paths: vi_name -> Path to original .vi file
        self._source_paths: dict[str, Path] = {}
        # VI metadata: library, qualified_name
        self._vi_metadata: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        """Clear all loaded data."""
        self._graph.clear()
        self._vi_nodes.clear()
        self._term_to_node.clear()
        self._dep_graph.clear()
        self._stubs.clear()
        self._poly_info.clear()
        self._qualified_aliases.clear()
        self._loaded_vis.clear()
        self._source_paths.clear()
        self._vi_metadata.clear()

    @staticmethod
    def _qid(vi_name: str, uid: str) -> str:
        """Qualify a parser UID with VI name to prevent cross-VI collisions."""
        return f"{vi_name}::{uid}"

    def _enrich_type(self, parsed_type: ParsedType | None) -> LVType | None:
        """Enrich ParsedType from parser to LVType with vilib data.

        Parser outputs ParsedType with basic info from single VI's XML.
        This enriches it with typedef details (enum values, cluster fields)
        from vilib_resolver.
        """
        if parsed_type is None:
            return None

        lv_type = LVType(
            kind=parsed_type.kind,
            underlying_type=parsed_type.type_name,
            ref_type=parsed_type.ref_type,
            classname=parsed_type.classname,
            typedef_path=parsed_type.typedef_path,
            typedef_name=parsed_type.typedef_name,
        )

        if parsed_type.typedef_name:
            resolver = get_vilib_resolver()
            resolved = resolver.resolve_type(parsed_type.typedef_name)
            if resolved:
                lv_type.values = resolved.values
                lv_type.fields = resolved.fields
                lv_type.description = resolved.description

        return lv_type

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
            fp_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", "_FPHb.xml"))
            if not fp_xml.exists():
                fp_xml = None
            main_xml = vi_path.with_name(vi_path.name.replace("_BDHb.xml", ".xml"))
            if not main_xml.exists():
                main_xml = None
        else:
            raise ValueError(f"Expected .vi or *_BDHb.xml file: {vi_path}")

        # Early return if already loaded (prevents re-parsing)
        vi_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        if vi_name in self._loaded_vis:
            return

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

    def load_lvlib(
        self,
        lvlib_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a .lvlib file."""
        lvlib_path = Path(lvlib_path)
        lib = parse_lvlib(lvlib_path)

        if search_paths is None:
            search_paths = [lvlib_path.parent]

        for member in lib.members:
            if member.member_type == "VI":
                vi_path = lvlib_path.parent / member.url
                if vi_path.exists():
                    self.load_vi(vi_path, expand_subvis, search_paths)

    def load_lvclass(
        self,
        lvclass_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a .lvclass file."""
        lvclass_path = Path(lvclass_path)
        cls = parse_lvclass(lvclass_path)

        if search_paths is None:
            search_paths = [lvclass_path.parent]

        for method in cls.methods:
            vi_path = self._resolve_class_vi_path(lvclass_path.parent, method.vi_path)
            if vi_path and vi_path.exists():
                self.load_vi(vi_path, expand_subvis, search_paths)

    def _resolve_class_vi_path(self, cls_dir: Path, relative_path: str) -> Path | None:
        """Resolve VI path from lvclass relative URL."""
        direct = cls_dir / relative_path
        if direct.exists():
            return direct.resolve()

        stripped = relative_path
        while stripped.startswith("../"):
            stripped = stripped[3:]
        if stripped != relative_path:
            from_cls = cls_dir / stripped
            if from_cls.exists():
                return from_cls.resolve()

        return None

    def load_lvproj(
        self,
        lvproj_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs referenced by a .lvproj file."""
        lvproj_path = Path(lvproj_path)
        proj = parse_lvproj(lvproj_path)
        proj_dir = lvproj_path.parent

        if search_paths is None:
            search_paths = [proj_dir]

        for lib_name, lib_path in get_project_libraries(proj):
            if lib_path.exists():
                self.load_lvlib(lib_path, expand_subvis, search_paths)

        for class_name, class_path in get_project_classes(proj):
            if class_path.exists():
                self.load_lvclass(class_path, expand_subvis, search_paths)

        for vi_name, vi_path in get_project_vis(proj):
            if vi_path.exists():
                self.load_vi(vi_path, expand_subvis, search_paths)

    def load_directory(
        self,
        dir_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a directory recursively."""
        dir_path = Path(dir_path)

        if search_paths is None:
            search_paths = [dir_path]

        for vi_path in dir_path.rglob("*.vi"):
            self.load_vi(vi_path, expand_subvis, search_paths)

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

        Returns the VI name (qualified if available) or None if already visited.
        """
        # Parse VI using unified parse_vi()
        vi = parse_vi(
            bd_xml=bd_xml,
            fp_xml=fp_xml if fp_xml and fp_xml.exists() else None,
            main_xml=main_xml if main_xml and main_xml.exists() else None,
        )

        metadata = vi.metadata
        bd = vi.block_diagram
        fp = vi.front_panel
        conpane = vi.connector_pane

        unqualified_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        vi_name = metadata.qualified_name or unqualified_name

        if vi_name in visited:
            return None

        if vi_name in self._loaded_vis:
            return vi_name

        if metadata.qualified_name and metadata.qualified_name != unqualified_name:
            self._qualified_aliases[unqualified_name] = metadata.qualified_name

        visited.add(vi_name)

        if metadata.source_path:
            self._source_paths[vi_name] = Path(metadata.source_path)

        # Parse wiring rules from main XML
        wiring_rules: dict[int, int] = {}
        if main_xml and main_xml.exists() and conpane:
            wiring_rules = parse_connector_pane_types(main_xml, conpane)

        type_map = metadata.type_map

        # Build the unified graph for this VI
        self._add_vi_to_graph(
            bd, fp, conpane, wiring_rules, vi_name, type_map
        )

        # Parse VI metadata for polymorphic info and library membership
        if main_xml and main_xml.exists():
            poly_metadata = parse_vi_metadata(main_xml)
            if poly_metadata.get("is_polymorphic"):
                self._poly_info[vi_name] = {
                    "is_polymorphic": True,
                    "variants": poly_metadata.get("poly_variants", []),
                    "selectors": poly_metadata.get("poly_selectors", []),
                }
            self._vi_metadata[vi_name] = {
                "library": poly_metadata.get("library"),
                "qualified_name": poly_metadata.get("qualified_name"),
            }

        # Add to dependency graph
        self._dep_graph.add_node(vi_name)

        # Mark as loaded
        self._loaded_vis.add(vi_name)

        # Process SubVIs
        if main_xml and main_xml.exists():
            subvi_ref_map = {
                ref.qualified_name: ref
                for ref in metadata.subvi_path_refs
                if ref.qualified_name
            }

            caller_dir = bd_xml.parent

            for subvi_qname in metadata.subvi_qualified_names:
                if subvi_qname == vi_name:
                    continue

                if subvi_qname in visited:
                    continue

                ref = subvi_ref_map.get(subvi_qname)
                if ref and ref.path_tokens:
                    lookup_path = ref.get_relative_path()
                    is_vilib = ref.is_vilib
                    is_userlib = ref.is_userlib
                else:
                    if ":" in subvi_qname:
                        lookup_path = subvi_qname.split(":")[-1]
                    else:
                        lookup_path = subvi_qname
                    is_vilib = False
                    is_userlib = False

                if expand_subvis:
                    subvi_path = self._find_subvi(
                        lookup_path, search_paths, caller_dir, is_vilib, is_userlib
                    )
                    if subvi_path:
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
                            self._stubs.add(subvi_qname)
                            self._dep_graph.add_node(subvi_qname)
                            self._dep_graph.add_edge(vi_name, subvi_qname)
                    else:
                        self._stubs.add(subvi_qname)
                        self._dep_graph.add_node(subvi_qname)
                        self._dep_graph.add_edge(vi_name, subvi_qname)
                else:
                    self._stubs.add(subvi_qname)
                    self._dep_graph.add_node(subvi_qname)
                    self._dep_graph.add_edge(vi_name, subvi_qname)

        return vi_name

    def _find_subvi(
        self,
        vi_path: str,
        search_paths: list[Path],
        caller_dir: Path | None = None,
        is_vilib: bool = False,
        is_userlib: bool = False,
    ) -> Path | None:
        """Find a SubVI file in search paths."""
        vi_name = Path(vi_path).name
        path_parts = Path(vi_path).parts

        if caller_dir and not is_vilib and not is_userlib:
            candidate = caller_dir / vi_name
            if candidate.exists():
                return candidate

            if len(path_parts) > 1:
                for parent in [caller_dir] + list(caller_dir.parents)[:3]:
                    candidate = parent / vi_path
                    if candidate.exists():
                        return candidate

        for search_path in search_paths:
            if len(path_parts) > 1:
                candidate = search_path / vi_path
                if candidate.exists():
                    return candidate

            candidate = search_path / vi_name
            if candidate.exists():
                return candidate

            for found in search_path.rglob(vi_name):
                return found
        return None

    @staticmethod
    def _format_lv_type_for_display(lv_type: LVType) -> str:
        """Format LVType for human-readable display."""
        if lv_type.kind == "primitive":
            return lv_type.underlying_type or "Any"
        elif lv_type.kind == "enum":
            if lv_type.typedef_name:
                name = lv_type.typedef_name.split(":")[-1].replace(".ctl", "")
                return name
            return "Enum"
        elif lv_type.kind == "cluster":
            if lv_type.typedef_name:
                name = lv_type.typedef_name.split(":")[-1].replace(".ctl", "")
                return name
            return "Cluster"
        elif lv_type.kind == "array":
            if lv_type.element_type:
                elem = InMemoryVIGraph._format_lv_type_for_display(
                    lv_type.element_type
                )
                return f"Array[{elem}]"
            return "Array"
        elif lv_type.kind == "ring":
            return "Ring"
        elif lv_type.kind == "typedef_ref":
            if lv_type.typedef_name:
                name = lv_type.typedef_name.split(":")[-1].replace(".ctl", "")
                return name
            return "TypeDef"
        else:
            return lv_type.underlying_type or "Any"

    # === Graph Construction ===

    def _add_vi_to_graph(
        self,
        bd: BlockDiagram,
        fp: FrontPanel | None,
        conpane: ConnectorPane | None,
        wiring_rules: dict[int, int],
        vi_name: str,
        type_map: dict[int, LVType] | None = None,
    ) -> None:
        """Add a VI's nodes and edges to the unified graph.

        Creates typed graph nodes (VINode, ConstantNode, PrimitiveNode,
        StructureNode) and typed edges (WireEnd source/dest).

        term_lookup is a LOCAL dict used during construction only.
        """
        if type_map is None:
            type_map = {}

        g = self._graph
        vi_node_uids: set[str] = set()

        # term_lookup: terminal_uid -> WireEnd (for wiring)
        term_lookup: dict[str, WireEnd] = {}

        # === 1. Build VINode (FP terminals become terminals on this node) ===

        # Build FP control lookup
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

        # Build FP terminals list for the VINode
        vi_terminals: list[Terminal] = []
        for fp_term in bd.fp_terminals:
            slot_index = conpane_slots.get(fp_term.fp_dco_uid)
            is_public = slot_index is not None or not conpane_slots
            direction = "output" if fp_term.is_indicator else "input"
            ctrl = fp_by_uid.get(fp_term.fp_dco_uid)
            wiring_rule = wiring_rules.get(slot_index, 0) if slot_index else 0

            # Resolve type
            lv_type = None
            control_type_str = ctrl.control_type if ctrl else None

            term_info = bd.terminal_info.get(fp_term.uid)
            if term_info and term_info.parsed_type:
                lv_type = self._enrich_type(term_info.parsed_type)

            if not lv_type and control_type_str:
                lv_type = control_type_to_lvtype(control_type_str)

            type_display = (
                self._format_lv_type_for_display(lv_type) if lv_type else "Any"
            )

            q_term_uid = self._qid(vi_name, fp_term.uid)
            terminal = FPTerminal(
                id=q_term_uid,
                index=slot_index if slot_index is not None else 0,
                direction=direction,
                name=ctrl.name if ctrl else fp_term.name,
                lv_type=lv_type,
                wiring_rule=wiring_rule,
                is_indicator=fp_term.is_indicator,
                is_public=is_public,
                control_type=ctrl.control_type if ctrl else None,
                default_value=ctrl.default_value if ctrl else None,
                enum_values=ctrl.enum_values if ctrl else [],
            )
            vi_terminals.append(terminal)

            # Register in term_lookup for wire resolution
            term_lookup[fp_term.uid] = WireEnd(
                terminal_id=q_term_uid,
                node_id=vi_name,
                index=slot_index,
                name=ctrl.name if ctrl else fp_term.name,
            )

        # Create the VINode
        vi_node = VINode(
            id=vi_name,
            vi=vi_name,
            name=vi_name,
            terminals=vi_terminals,
        )
        g.add_node(vi_name, node=vi_node)
        vi_node_uids.add(vi_name)

        # === 2. Add Constants ===
        for const in bd.constants:
            val_type, decoded_value = decode_constant(const)

            lv_type = None
            term_info = bd.terminal_info.get(const.uid)
            if term_info and term_info.parsed_type:
                lv_type = self._enrich_type(term_info.parsed_type)

            q_const_uid = self._qid(vi_name, const.uid)
            # Single output terminal
            const_terminal = Terminal(
                id=q_const_uid,
                index=0,
                direction="output",
                lv_type=lv_type,
            )

            const_node = ConstantNode(
                id=q_const_uid,
                vi=vi_name,
                value=decoded_value,
                lv_type=lv_type,
                raw_value=const.value,
                label=const.label,
                terminals=[const_terminal],
            )
            g.add_node(q_const_uid, node=const_node)
            vi_node_uids.add(q_const_uid)

            term_lookup[const.uid] = WireEnd(
                terminal_id=q_const_uid,
                node_id=q_const_uid,
                index=0,
                name=const.label,
            )

        # === 3. Add operations (SubVIs, primitives, structures) ===

        # Collect structure info indexed by UID for later use
        loop_by_uid = {loop.uid: loop for loop in bd.loops}
        case_by_uid = {cs.uid: cs for cs in bd.case_structures}
        flatseq_by_uid = {fs.uid: fs for fs in bd.flat_sequences}

        for node in bd.nodes:
            q_node_uid = self._qid(vi_name, node.uid)
            # Collect terminals for this node
            node_terminals: list[Terminal] = []
            for term_uid, t_info in bd.terminal_info.items():
                if t_info.parent_uid == node.uid:
                    lv_type = None
                    if t_info.parsed_type:
                        lv_type = self._enrich_type(t_info.parsed_type)

                    q_term_uid = self._qid(vi_name, term_uid)
                    terminal = Terminal(
                        id=q_term_uid,
                        index=t_info.index,
                        direction="output" if t_info.is_output else "input",
                        name=t_info.name,
                        lv_type=lv_type,
                    )
                    node_terminals.append(terminal)

                    term_lookup[term_uid] = WireEnd(
                        terminal_id=q_term_uid,
                        node_id=q_node_uid,
                        index=t_info.index,
                        name=t_info.name,
                    )

            node_terminals.sort(key=lambda t: t.index)

            # Resolve node name
            node_name = node.name
            if isinstance(node, ParserPrimitiveNode) and node.prim_res_id:
                resolved = resolve_primitive(prim_id=node.prim_res_id)
                if resolved:
                    node_name = resolved.name

            if not node_name and node.node_type:
                resolved_nt = get_prim_resolver().resolve_by_node_type(node.node_type)
                if resolved_nt:
                    node_name = resolved_nt.name

            # Get description for SubVIs from vilib
            description = None
            if node.node_type in ("iUse", "polyIUse", "dynIUse") and node_name:
                vilib_r = get_vilib_resolver()
                vi_entry = vilib_r.resolve_by_name(node_name)
                if vi_entry and vi_entry.description:
                    description = vi_entry.description

            # Determine what kind of graph node to create
            if node.node_type in ("iUse", "polyIUse", "dynIUse"):
                # SubVI call — stored as VINode
                poly_variant = None
                if isinstance(node, SubVINode) and node.poly_variant_name:
                    poly_variant = node.poly_variant_name
                graph_node: AnyGraphNode = VINode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=node_terminals,
                    description=description,
                    poly_variant_name=poly_variant,
                )
            elif node.node_type in ("whileLoop", "forLoop"):
                # Loop structure
                loop_struct = loop_by_uid.get(node.uid)
                stop_cond: str | None = None

                parser_tunnels: list = []
                if loop_struct:
                    parser_tunnels = loop_struct.tunnels
                    if loop_struct.stop_condition_terminal_uid:
                        stop_cond = self._qid(
                            vi_name, loop_struct.stop_condition_terminal_uid
                        )

                # Build terminals from tunnels + sRN terminals
                structure_terminals = self._build_structure_terminals(
                    bd, parser_tunnels, q_node_uid, term_lookup, vi_name,
                )

                graph_node = StructureNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=structure_terminals,
                    loop_type=node.node_type,
                    stop_condition_terminal=stop_cond,
                )
            elif node.node_type in ("caseStruct", "select"):
                # Case structure
                case_struct = case_by_uid.get(node.uid)
                frame_infos: list[FrameInfo] = []
                selector_term: str | None = None

                parser_tunnels = []
                if case_struct:
                    parser_tunnels = case_struct.tunnels
                    if case_struct.selector_terminal_uid:
                        selector_term = self._qid(
                            vi_name, case_struct.selector_terminal_uid
                        )
                    frame_infos = [
                        FrameInfo(
                            selector_value=pf.selector_value,
                            is_default=pf.is_default,
                        )
                        for pf in case_struct.frames
                    ]

                # Build terminals from tunnels + sRN terminals
                structure_terminals = self._build_structure_terminals(
                    bd, parser_tunnels, q_node_uid, term_lookup, vi_name,
                )

                graph_node = StructureNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=structure_terminals,
                    frames=frame_infos,
                    selector_terminal=selector_term,
                )
            elif node.node_type in ("flatSequence", "seq"):
                # Flat sequence
                flat_seq = flatseq_by_uid.get(node.uid)
                seq_frame_infos: list[FrameInfo] = []

                parser_tunnels = []
                if flat_seq:
                    parser_tunnels = flat_seq.tunnels
                    seq_frame_infos = [
                        FrameInfo(
                            selector_value=str(idx),
                        )
                        for idx in range(len(flat_seq.frames))
                    ]

                # Build terminals from tunnels + sRN terminals
                structure_terminals = self._build_structure_terminals(
                    bd, parser_tunnels, q_node_uid, term_lookup, vi_name,
                )

                graph_node = StructureNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=structure_terminals,
                    frames=seq_frame_infos,
                )
            elif isinstance(node, ParserPrimitiveNode):
                # Primitive node
                prim_kwargs: dict[str, Any] = {
                    "prim_id": node.prim_res_id,
                    "prim_index": node.prim_index,
                }
                if isinstance(node, CpdArithNode):
                    prim_kwargs["operation"] = node.operation
                if isinstance(node, PropertyNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["properties"] = [
                        PropertyDef(name=p.get("name", ""))
                        if isinstance(p, dict) else p
                        for p in node.properties
                    ]
                if isinstance(node, InvokeNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["method_name"] = node.method_name
                    prim_kwargs["method_code"] = node.method_code

                graph_node = GraphPrimitiveNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=node_terminals,
                    description=description,
                    **prim_kwargs,
                )
            else:
                # Generic primitive / operation
                prim_kwargs = {}
                if isinstance(node, CpdArithNode):
                    prim_kwargs["operation"] = node.operation
                if isinstance(node, PropertyNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["properties"] = [
                        PropertyDef(name=p.get("name", ""))
                        if isinstance(p, dict) else p
                        for p in node.properties
                    ]
                if isinstance(node, InvokeNode):
                    prim_kwargs["object_name"] = node.object_name
                    prim_kwargs["object_method_id"] = node.object_method_id
                    prim_kwargs["method_name"] = node.method_name
                    prim_kwargs["method_code"] = node.method_code

                graph_node = GraphPrimitiveNode(
                    id=q_node_uid,
                    vi=vi_name,
                    name=node_name,
                    node_type=node.node_type,
                    terminals=node_terminals,
                    description=description,
                    **prim_kwargs,
                )

            g.add_node(q_node_uid, node=graph_node)
            vi_node_uids.add(q_node_uid)

        # === 4. Set parent/frame on inner operation nodes ===
        # After all nodes are created, walk parser structures and stamp
        # containment info on the graph nodes they own.

        for loop in bd.loops:
            q_loop_uid = self._qid(vi_name, loop.uid)
            for uid in loop.inner_node_uids:
                q_uid = self._qid(vi_name, uid)
                if q_uid in g and "node" in g.nodes[q_uid]:
                    inner_node = g.nodes[q_uid]["node"]
                    inner_node.parent = q_loop_uid
                    inner_node.frame = None

        for cs in bd.case_structures:
            q_cs_uid = self._qid(vi_name, cs.uid)
            for frame in cs.frames:
                for uid in frame.inner_node_uids:
                    q_uid = self._qid(vi_name, uid)
                    if q_uid in g and "node" in g.nodes[q_uid]:
                        inner_node = g.nodes[q_uid]["node"]
                        inner_node.parent = q_cs_uid
                        inner_node.frame = frame.selector_value

        for fs in bd.flat_sequences:
            q_fs_uid = self._qid(vi_name, fs.uid)
            for idx, frame in enumerate(fs.frames):
                for uid in frame.inner_node_uids:
                    q_uid = self._qid(vi_name, uid)
                    if q_uid in g and "node" in g.nodes[q_uid]:
                        inner_node = g.nodes[q_uid]["node"]
                        inner_node.parent = q_fs_uid
                        inner_node.frame = str(idx)

        # === 5. Register remaining terminal_info entries in term_lookup ===
        # Most tunnel/sRN terminals are already registered by
        # _build_structure_terminals. This catches any stragglers whose
        # parent is not a recognized graph node (e.g., orphan sRN
        # terminals not referenced by any tunnel).
        for term_uid, t_info in bd.terminal_info.items():
            if term_uid not in term_lookup:
                q_term_uid = self._qid(vi_name, term_uid)
                parent_uid = t_info.parent_uid
                q_parent_uid = (
                    self._qid(vi_name, parent_uid) if parent_uid else None
                )
                effective_parent = q_parent_uid
                # If parent is not a graph node, find the structure
                # that contains it. Check both terminal lists and
                # parser structure inner_node_uids (catches sRNs
                # not referenced by tunnels).
                if q_parent_uid and q_parent_uid not in g:
                    # First: check structure terminal lists
                    for s_uid in vi_node_uids:
                        if s_uid not in g:
                            continue
                        snode = g.nodes[s_uid].get("node")
                        if isinstance(snode, StructureNode):
                            for st in snode.terminals:
                                if st.id == q_term_uid:
                                    effective_parent = s_uid
                                    break
                            if effective_parent == s_uid:
                                break
                    # Second: check parser structures for containment
                    if effective_parent == q_parent_uid:
                        for cs in bd.case_structures:
                            for frame in cs.frames:
                                if parent_uid in frame.inner_node_uids:
                                    effective_parent = self._qid(vi_name, cs.uid)
                                    break
                            if effective_parent != q_parent_uid:
                                break
                    if effective_parent == q_parent_uid:
                        for loop in bd.loops:
                            if parent_uid in loop.inner_node_uids:
                                effective_parent = self._qid(vi_name, loop.uid)
                                break
                    if effective_parent == q_parent_uid:
                        for fs in bd.flat_sequences:
                            for frame in fs.frames:
                                if parent_uid in frame.inner_node_uids:
                                    effective_parent = self._qid(vi_name, fs.uid)
                                    break
                            if effective_parent != q_parent_uid:
                                break

                term_lookup[term_uid] = WireEnd(
                    terminal_id=q_term_uid,
                    node_id=effective_parent or q_term_uid,
                    index=t_info.index,
                    name=t_info.name,
                )

        # === 6. Add edges (wires) ===
        for wire in bd.wires:
            src_end = term_lookup.get(wire.from_term)
            dst_end = term_lookup.get(wire.to_term)

            if src_end is None:
                q_from = self._qid(vi_name, wire.from_term)
                src_end = WireEnd(
                    terminal_id=q_from,
                    node_id=q_from,
                )
            if dst_end is None:
                q_to = self._qid(vi_name, wire.to_term)
                dst_end = WireEnd(
                    terminal_id=q_to,
                    node_id=q_to,
                )

            g.add_edge(
                src_end.node_id,
                dst_end.node_id,
                source=src_end,
                dest=dst_end,
                vi=vi_name,
            )

        # Store per-VI node index
        self._vi_nodes[vi_name] = vi_node_uids

        # Populate terminal ownership from term_lookup
        # Keys are raw parser UIDs but WireEnd.terminal_id is qualified.
        # Use qualified terminal_id as the key in _term_to_node.
        for _raw_tid, wire_end in term_lookup.items():
            self._term_to_node[wire_end.terminal_id] = wire_end.node_id

    # Tunnel types where the outer terminal is an input (data flows IN)
    _INPUT_TUNNEL_TYPES = frozenset({
        "lSR", "lpTun", "caseSel", "seqTun", "flatSeqTun",
    })

    def _build_structure_terminals(
        self,
        bd: BlockDiagram,
        parser_tunnels: list,
        structure_uid: str,
        term_lookup: dict[str, WireEnd],
        vi_name: str = "",
    ) -> list[Terminal]:
        """Build Terminal list for a StructureNode from its tunnels and sRN nodes.

        Each parser tunnel creates TWO Terminal objects:
        - Outer terminal (boundary="outer")
        - Inner terminal (boundary="inner")

        Also maps sRN-owned terminals to the structure and creates
        internal edges (self-loops) on the graph for:
        - Tunnel outer<->inner connections
        - sRN input->output pairings

        Returns the complete terminal list for the StructureNode.
        """
        g = self._graph
        structure_terminals: list[Terminal] = []
        seen_uids: set[str] = set()

        # Collect known parser node UIDs for sRN detection
        known_node_uids = {n.uid for n in bd.nodes}

        # --- 1. Build terminals from tunnel mappings ---
        for tunnel in parser_tunnels:
            outer_uid = tunnel.outer_terminal_uid
            inner_uid = tunnel.inner_terminal_uid
            ttype = tunnel.tunnel_type

            if not outer_uid or not inner_uid:
                continue

            outer_ti = bd.terminal_info.get(outer_uid)
            inner_ti = bd.terminal_info.get(inner_uid)

            # Determine direction from terminal_info, not tunnel type.
            # selTun tunnels are bidirectional — direction depends on instance.
            # If outer is_output=False, data flows IN (outer receives from outside).
            # If outer is_output=True, data flows OUT (outer sends to outside).
            if outer_ti:
                is_input_tunnel = not outer_ti.is_output
            else:
                is_input_tunnel = ttype in self._INPUT_TUNNEL_TYPES

            q_outer_uid = self._qid(vi_name, outer_uid)
            q_inner_uid = self._qid(vi_name, inner_uid)

            # Outer terminal
            outer_lv_type = None
            if outer_ti and outer_ti.parsed_type:
                outer_lv_type = self._enrich_type(outer_ti.parsed_type)

            outer_terminal = TunnelTerminal(
                id=q_outer_uid,
                index=outer_ti.index if outer_ti else 0,
                direction="input" if is_input_tunnel else "output",
                name=outer_ti.name if outer_ti else None,
                lv_type=outer_lv_type,
                tunnel_type=ttype,
                boundary="outer",
                paired_id=q_inner_uid,
            )
            if outer_uid not in seen_uids:
                structure_terminals.append(outer_terminal)
                seen_uids.add(outer_uid)

            # Inner terminal
            inner_lv_type = None
            if inner_ti and inner_ti.parsed_type:
                inner_lv_type = self._enrich_type(inner_ti.parsed_type)

            # Inner direction is opposite of outer for data flow
            inner_terminal = TunnelTerminal(
                id=q_inner_uid,
                index=inner_ti.index if inner_ti else 0,
                direction="output" if is_input_tunnel else "input",
                name=inner_ti.name if inner_ti else None,
                lv_type=inner_lv_type,
                tunnel_type=ttype,
                boundary="inner",
                paired_id=q_outer_uid,
            )
            if inner_uid not in seen_uids:
                structure_terminals.append(inner_terminal)
                seen_uids.add(inner_uid)

            # Register both in term_lookup pointing to structure node
            outer_end = WireEnd(
                terminal_id=q_outer_uid,
                node_id=structure_uid,
                index=outer_ti.index if outer_ti else None,
                name=outer_ti.name if outer_ti else None,
            )
            inner_end = WireEnd(
                terminal_id=q_inner_uid,
                node_id=structure_uid,
                index=inner_ti.index if inner_ti else None,
                name=inner_ti.name if inner_ti else None,
            )
            term_lookup[outer_uid] = outer_end
            term_lookup[inner_uid] = inner_end

            # Create internal edge (self-loop) outer<->inner
            if is_input_tunnel:
                # Data flows in: outer -> inner
                g.add_edge(
                    structure_uid, structure_uid,
                    source=outer_end, dest=inner_end,
                    tunnel_type=ttype, vi=vi_name,
                )
            else:
                # Data flows out: inner -> outer
                g.add_edge(
                    structure_uid, structure_uid,
                    source=inner_end, dest=outer_end,
                    tunnel_type=ttype, vi=vi_name,
                )

        # --- 2. Find ALL sRN parent UIDs ---
        # Tunnel-referenced sRNs get input→output pairing edges.
        # Non-tunnel sRNs just get mapped — wires handle routing.
        tunnel_srn_parents: set[str] = set()
        for tunnel in parser_tunnels:
            for uid in (tunnel.outer_terminal_uid, tunnel.inner_terminal_uid):
                if not uid:
                    continue
                ti = bd.terminal_info.get(uid)
                if ti and ti.parent_uid and ti.parent_uid not in known_node_uids:
                    tunnel_srn_parents.add(ti.parent_uid)

        all_srn_parents: set[str] = set()
        for uid, ti in bd.terminal_info.items():
            if ti.parent_uid and ti.parent_uid not in known_node_uids:
                all_srn_parents.add(ti.parent_uid)

        for srn_uid in all_srn_parents:
            # Collect all terminals owned by this sRN
            srn_terms = [
                (uid, ti) for uid, ti in bd.terminal_info.items()
                if ti.parent_uid == srn_uid
            ]

            inputs = sorted(
                [(uid, ti) for uid, ti in srn_terms if not ti.is_output],
                key=lambda x: x[1].index,
            )
            outputs = sorted(
                [(uid, ti) for uid, ti in srn_terms if ti.is_output],
                key=lambda x: x[1].index,
            )

            # Add sRN terminals to structure — but skip ones already
            # registered (constants, FP terminals have their own nodes)
            for uid, ti in srn_terms:
                if uid in seen_uids or uid in term_lookup:
                    continue
                seen_uids.add(uid)

                q_uid = self._qid(vi_name, uid)
                lv_type = None
                if ti.parsed_type:
                    lv_type = self._enrich_type(ti.parsed_type)

                structure_terminals.append(Terminal(
                    id=q_uid,
                    index=ti.index,
                    direction="output" if ti.is_output else "input",
                    name=ti.name,
                    lv_type=lv_type,
                ))

                term_lookup[uid] = WireEnd(
                    terminal_id=q_uid,
                    node_id=structure_uid,
                    index=ti.index,
                    name=ti.name,
                )

            # Pair by matching index (same position on structure border)
            # — same as VI connector pane pairing
            input_by_idx = {ti.index: (uid, ti) for uid, ti in srn_terms if not ti.is_output}
            output_by_idx = {ti.index: (uid, ti) for uid, ti in srn_terms if ti.is_output}
            paired = [(input_by_idx[idx], output_by_idx[idx]) for idx in input_by_idx if idx in output_by_idx]
            for (in_uid, _in_ti), (out_uid, _out_ti) in paired:
                q_in_uid = self._qid(vi_name, in_uid)
                q_out_uid = self._qid(vi_name, out_uid)
                in_end = term_lookup.get(in_uid, WireEnd(
                    terminal_id=q_in_uid, node_id=structure_uid,
                ))
                out_end = term_lookup.get(out_uid, WireEnd(
                    terminal_id=q_out_uid, node_id=structure_uid,
                ))
                g.add_edge(
                    structure_uid, structure_uid,
                    source=in_end, dest=out_end,
                    tunnel_type="sRN", vi=vi_name,
                )

        return structure_terminals

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
        properties: list[dict[str, Any]] = []
        method_name: str | None = None
        method_code: int | None = None
        poly_variant_name: str | None = None

        if isinstance(gnode, GraphPrimitiveNode):
            prim_id = gnode.prim_id
            operation = gnode.operation
            object_name = gnode.object_name
            object_method_id = gnode.object_method_id
            properties = [{"name": p.name} for p in gnode.properties]
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

    def get_operation_order(self, vi_name: str) -> list[str]:
        """Get operations in dataflow execution order.

        Returns operation node IDs in the order they should execute
        (topological sort based on wire connections).
        """
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        # Get operation node IDs
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
            if op_kind in _OPERATION_KINDS:
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

        # Also check edges FROM constants and VINode (FP terminals) to ops
        # A constant feeding into an op means the op has a dependency on
        # its input being available (but constants don't need ordering).
        # We need edges between ops that share intermediate wiring.

        # For edges through the VINode (FP terminals) or constants to ops:
        # these are not op-to-op edges, but we need to consider transitive
        # dependencies. The unified graph has edges: const->op, vi->op, op->vi
        # so direct op->op edges from the graph capture the dependencies.

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

    def set_var_name(self, terminal_id: str, var_name: str) -> None:
        """Set the Python variable name on a terminal. Called during codegen."""
        node_id = self._term_to_node.get(terminal_id)
        if not node_id or not self._graph.has_node(node_id):
            return
        gnode = self._graph.nodes[node_id].get("node")
        if not gnode:
            return
        for t in gnode.terminals:
            if t.id == terminal_id:
                t.var_name = var_name
                return

    def get_var_name(self, terminal_id: str) -> str | None:
        """Get the Python variable name from a terminal."""
        node_id = self._term_to_node.get(terminal_id)
        if not node_id or not self._graph.has_node(node_id):
            return None
        gnode = self._graph.nodes[node_id].get("node")
        if not gnode:
            return None
        for t in gnode.terminals:
            if t.id == terminal_id:
                return t.var_name
        return None

    def incoming_edges(self, terminal_id: str) -> list[WireEnd]:
        """Get all source WireEnds that feed into a terminal."""
        node_id = self._term_to_node.get(terminal_id)
        if not node_id or not self._graph.has_node(node_id):
            return []
        results = []
        for _, _, _, d in self._graph.in_edges(node_id, data=True, keys=True):
            dst = d.get("dest")
            if dst and dst.terminal_id == terminal_id:
                src = d.get("source")
                if src:
                    results.append(src)
        return results

    def outgoing_edges(self, terminal_id: str) -> list[WireEnd]:
        """Get all dest WireEnds that a terminal feeds into."""
        node_id = self._term_to_node.get(terminal_id)
        if not node_id or not self._graph.has_node(node_id):
            return []
        results = []
        for _, _, _, d in self._graph.out_edges(node_id, data=True, keys=True):
            src = d.get("source")
            if src and src.terminal_id == terminal_id:
                dst = d.get("dest")
                if dst:
                    results.append(dst)
        return results

    def terminal_is_wired(self, terminal_id: str) -> bool:
        """Check if a terminal has any edge connected."""
        node_id = self._term_to_node.get(terminal_id)
        if not node_id or not self._graph.has_node(node_id):
            return False
        for _, _, _, d in self._graph.in_edges(node_id, data=True, keys=True):
            dst = d.get("dest")
            if dst and dst.terminal_id == terminal_id:
                return True
        for _, _, _, d in self._graph.out_edges(node_id, data=True, keys=True):
            src = d.get("source")
            if src and src.terminal_id == terminal_id:
                return True
        return False

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

    def _kind_to_labels(self, kind: str) -> list[str]:
        """Convert internal kind to labels list."""
        if kind == "vi":
            return ["SubVI"]
        elif kind == "primitive":
            return ["Primitive"]
        elif kind == "operation":
            return ["Operation"]
        elif kind == "constant":
            return ["Constant"]
        elif kind == "vi":
            return ["Control", "Input"]
        return []

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

        vi_meta = self._vi_metadata.get(vi_name, {})

        return {
            "name": vi_name,
            "library": vi_meta.get("library"),
            "qualified_name": vi_meta.get("qualified_name"),
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
        info = self._poly_info.get(vi_name, {})
        return info.get("variants", [])

    def get_polymorphic_groups(self) -> dict[str, list[str]]:
        """Get all polymorphic VIs and their variants."""
        return {
            vi_name: info["variants"]
            for vi_name, info in self._poly_info.items()
            if info.get("variants")
        }

    def get_poly_variant_wrappers(self) -> dict[str, str]:
        """Get mapping of variant VI names to their wrapper VI."""
        result: dict[str, str] = {}
        for wrapper, variants in self._poly_info.items():
            for variant in variants.get("variants", []):
                result[variant] = wrapper
        return result

    # === Parallel Branch Detection ===

    def find_branch_points(self, vi_name: str) -> list[BranchPoint]:
        """Find terminals where one output feeds multiple inputs."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return []

        branch_points: list[BranchPoint] = []

        for uid in node_uids:
            if uid not in self._graph:
                continue
            successors = list(self._graph.successors(uid))
            if len(successors) > 1:
                gnode = self._graph.nodes[uid].get("node")
                source_op = uid if gnode else None

                branch_points.append(BranchPoint(
                    source_terminal=uid,
                    source_operation=source_op,
                    destinations=successors,
                    vi_name=vi_name,
                ))

        return branch_points

    def trace_branch(
        self,
        vi_name: str,
        start_terminal: str,
        all_branch_starts: set[str],
    ) -> ParallelBranch:
        """Trace a single branch from a start terminal to its merge point."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return ParallelBranch(
                branch_id=0,
                source_terminal=start_terminal,
                operation_ids=[],
                merge_terminal=None,
                merge_operation=None,
            )

        visited: set[str] = set()
        operations: list[str] = []
        merge_terminal: str | None = None
        merge_operation: str | None = None

        def trace(node_id: str) -> bool:
            nonlocal merge_terminal, merge_operation

            if node_id in visited:
                return False
            visited.add(node_id)

            if node_id not in self._graph:
                return False
            gnode = self._graph.nodes[node_id].get("node")

            # If we hit the VINode (an output terminal), branch ends
            if isinstance(gnode, VINode) and gnode.id == gnode.vi:
                merge_terminal = node_id
                return True

            # If this is an operation, collect it
            if gnode is not None:
                op_kind = _graph_node_to_op_kind(gnode)
                if op_kind in _OPERATION_KINDS:
                    operations.append(node_id)

            successors = list(self._graph.successors(node_id))

            for succ in successors:
                predecessors = list(self._graph.predecessors(succ))
                other_inputs = [
                    p for p in predecessors
                    if p != node_id and p in all_branch_starts
                ]
                if other_inputs:
                    merge_terminal = succ
                    merge_operation = succ
                    return True

                if trace(succ):
                    return True

            return False

        trace(start_terminal)

        return ParallelBranch(
            branch_id=0,
            source_terminal=start_terminal,
            operation_ids=operations,
            merge_terminal=merge_terminal,
            merge_operation=merge_operation,
        )

    def get_parallel_branches(
        self, vi_name: str
    ) -> list[tuple[BranchPoint, list[ParallelBranch]]]:
        """Get all parallel branch structures in a VI."""
        branch_points = self.find_branch_points(vi_name)
        result: list[tuple[BranchPoint, list[ParallelBranch]]] = []

        for bp in branch_points:
            branches: list[ParallelBranch] = []
            all_starts = set(bp.destinations)

            for i, dest in enumerate(bp.destinations):
                branch = self.trace_branch(vi_name, dest, all_starts)
                branch = ParallelBranch(
                    branch_id=i,
                    source_terminal=branch.source_terminal,
                    operation_ids=branch.operation_ids,
                    merge_terminal=branch.merge_terminal,
                    merge_operation=branch.merge_operation,
                )
                branches.append(branch)

            result.append((bp, branches))

        return result

    def has_parallel_branches(self, vi_name: str) -> bool:
        """Check if a VI has any parallel branch points."""
        node_uids = self._vi_nodes.get(vi_name)
        if node_uids is None:
            return False

        for uid in node_uids:
            if uid not in self._graph:
                continue
            successors = list(self._graph.successors(uid))
            if len(successors) > 1:
                return True

        return False

    # === Context Manager ===

    def __enter__(self) -> InMemoryVIGraph:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.clear()


def connect() -> InMemoryVIGraph:
    """Create an in-memory VI graph (no connection needed)."""
    return InMemoryVIGraph()

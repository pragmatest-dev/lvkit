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
from .graph_types import (
    BranchPoint,
    CaseFrame,
    Constant,
    FPTerminalNode,
    LVType,
    Operation,
    ParallelBranch,
    Terminal,
    Tunnel,
    Wire,
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
from .parser.node_types import CpdArithNode, InvokeNode, PrimitiveNode, PropertyNode, SubVINode
from .primitive_resolver import get_resolver as get_prim_resolver
from .primitive_resolver import resolve_primitive
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
    "subvi": ["SubVI"],
    "primitive": ["Primitive"],
    "caseStruct": ["CaseStructure"],
    "loop": ["Loop"],
}

# Kinds that represent executable operations (for ordering/collection)
_OPERATION_KINDS = ("subvi", "primitive", "operation", "caseStruct", "loop")


def _get_operation_labels(kind: str) -> list[str]:
    """Get labels for an operation based on its kind.

    Args:
        kind: Operation kind ("subvi", "primitive", or other)

    Returns:
        List of labels for the operation
    """
    return _KIND_TO_LABELS.get(kind, ["Operation"])


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
        # Case structures: vi_name -> {case_uid -> CaseStructure}
        self._case_structures: dict[str, dict[str, Any]] = {}
        # Flat sequence structures: vi_name -> {seq_uid -> FlatSequenceStructure}
        self._flat_sequences: dict[str, dict[str, Any]] = {}
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
        self._dep_graph.clear()
        self._dataflow.clear()
        self._stubs.clear()
        self._bindings.clear()
        self._loop_structures.clear()
        self._case_structures.clear()
        self._flat_sequences.clear()
        self._qualified_aliases.clear()
        self._poly_info.clear()
        self._loaded_vis.clear()
        self._source_paths.clear()

    def _enrich_type(self, parsed_type: ParsedType | None) -> LVType | None:
        """Enrich ParsedType from parser to LVType with vilib data.

        Parser outputs ParsedType with basic info from single VI's XML.
        This enriches it with typedef details (enum values, cluster fields)
        from vilib_resolver.

        Args:
            parsed_type: ParsedType from parser

        Returns:
            Enriched LVType with values/fields from vilib_resolver
        """
        if parsed_type is None:
            return None

        # Start with basic LVType from ParsedType
        lv_type = LVType(
            kind=parsed_type.kind,
            underlying_type=parsed_type.type_name,
            ref_type=parsed_type.ref_type,
            classname=parsed_type.classname,
            typedef_path=parsed_type.typedef_path,
            typedef_name=parsed_type.typedef_name,
        )

        # Enrich typedefs with vilib_resolver data (enum values, cluster fields)
        if parsed_type.typedef_name:
            resolver = get_vilib_resolver()
            resolved = resolver.resolve_type(parsed_type.typedef_name)
            if resolved:
                lv_type.values = resolved.values
                lv_type.fields = resolved.fields
                lv_type.description = resolved.description

        return lv_type

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

        # Build cross-VI bindings after all VIs are loaded
        if expand_subvis:
            self._build_cross_vi_bindings()

    def load_lvlib(
        self,
        lvlib_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a .lvlib file.

        Args:
            lvlib_path: Path to .lvlib file
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
        """
        from .structure import parse_lvlib

        lvlib_path = Path(lvlib_path)
        lib = parse_lvlib(lvlib_path)

        # Default search path is the library's directory
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
        """Load all VIs from a .lvclass file.

        Args:
            lvclass_path: Path to .lvclass file
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
        """
        from .structure import parse_lvclass

        lvclass_path = Path(lvclass_path)
        cls = parse_lvclass(lvclass_path)

        # Default search path is the class's directory
        if search_paths is None:
            search_paths = [lvclass_path.parent]

        for method in cls.methods:
            vi_path = self._resolve_class_vi_path(lvclass_path.parent, method.vi_path)
            if vi_path and vi_path.exists():
                self.load_vi(vi_path, expand_subvis, search_paths)

    def _resolve_class_vi_path(self, cls_dir: Path, relative_path: str) -> Path | None:
        """Resolve VI path from lvclass relative URL.

        LabVIEW stores paths with extra ../ that don't match filesystem layout.
        """
        # Try direct resolution first
        direct = cls_dir / relative_path
        if direct.exists():
            return direct.resolve()

        # Strip ../ prefixes and resolve from class dir
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
        """Load all VIs referenced by a .lvproj file.

        Parses the project file and loads all VIs, classes, and libraries
        that are explicitly included in the project.

        Args:
            lvproj_path: Path to .lvproj file
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
        """
        from .structure import (
            get_project_classes,
            get_project_libraries,
            get_project_vis,
            parse_lvproj,
        )

        lvproj_path = Path(lvproj_path)
        proj = parse_lvproj(lvproj_path)
        proj_dir = lvproj_path.parent

        # Default search path is the project's directory
        if search_paths is None:
            search_paths = [proj_dir]

        # Load all libraries first (they may contain VIs referenced by other items)
        for lib_name, lib_path in get_project_libraries(proj):
            if lib_path.exists():
                self.load_lvlib(lib_path, expand_subvis, search_paths)

        # Load all classes
        for class_name, class_path in get_project_classes(proj):
            if class_path.exists():
                self.load_lvclass(class_path, expand_subvis, search_paths)

        # Load standalone VIs
        for vi_name, vi_path in get_project_vis(proj):
            if vi_path.exists():
                self.load_vi(vi_path, expand_subvis, search_paths)

    def load_directory(
        self,
        dir_path: Path | str,
        expand_subvis: bool = True,
        search_paths: list[Path] | None = None,
    ) -> None:
        """Load all VIs from a directory recursively.

        Args:
            dir_path: Directory to scan for VIs
            expand_subvis: Recursively expand SubVIs
            search_paths: Directories to search for SubVIs
        """
        dir_path = Path(dir_path)

        # Default search path is the directory itself
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
        The visited set tracks qualified names (e.g., "Library.lvlib:VI.vi").
        """
        # Parse VI using unified parse_vi()
        vi = parse_vi(
            bd_xml=bd_xml,
            fp_xml=fp_xml if fp_xml and fp_xml.exists() else None,
            main_xml=main_xml if main_xml and main_xml.exists() else None,
        )

        # Unpack components
        metadata = vi.metadata
        bd = vi.block_diagram
        fp = vi.front_panel
        conpane = vi.connector_pane

        # Use qualified name from metadata, fall back to filename
        unqualified_name = bd_xml.name.replace("_BDHb.xml", ".vi")
        vi_name = metadata.qualified_name or unqualified_name

        # Check if already visited using qualified name
        if vi_name in visited:
            return None

        # Also check instance-level cache to prevent re-parsing across load_vi() calls
        if vi_name in self._loaded_vis:
            return vi_name

        # Store alias for unqualified -> qualified lookup
        if metadata.qualified_name and metadata.qualified_name != unqualified_name:
            self._qualified_aliases[unqualified_name] = metadata.qualified_name

        visited.add(vi_name)

        # Store source file path from metadata
        if metadata.source_path:
            self._source_paths[vi_name] = Path(metadata.source_path)

        # Parse wiring rules from main XML if available
        wiring_rules: dict[int, int] = {}
        if main_xml and main_xml.exists() and conpane:
            wiring_rules = parse_connector_pane_types(main_xml, conpane)

        # Use type_map from metadata (already parsed)
        type_map = metadata.type_map

        # Build dataflow graph for this VI
        self._dataflow[vi_name] = self._build_dataflow_graph(
            bd, fp, conpane, wiring_rules, vi_name, type_map
        )

        # Store loop structures for later lookup
        self._loop_structures[vi_name] = {loop.uid: loop for loop in bd.loops}

        # Store case structures for later lookup
        self._case_structures[vi_name] = {
            cs.uid: cs for cs in bd.case_structures
        }

        # Store flat sequence structures for later lookup
        self._flat_sequences[vi_name] = {
            fs.uid: fs for fs in bd.flat_sequences
        }

        # Parse VI metadata for polymorphic info and library membership
        if main_xml and main_xml.exists():
            poly_metadata = parse_vi_metadata(main_xml)
            if poly_metadata.get("is_polymorphic"):
                self._poly_info[vi_name] = {
                    "is_polymorphic": True,
                    "variants": poly_metadata.get("poly_variants", []),
                    "selectors": poly_metadata.get("poly_selectors", []),
                }
            # Store library/class membership
            self._vi_metadata[vi_name] = {
                "library": poly_metadata.get("library"),
                "qualified_name": poly_metadata.get("qualified_name"),
            }

        # Add to dependency graph
        self._dep_graph.add_node(vi_name)

        # Mark as loaded to prevent re-parsing in recursive calls
        self._loaded_vis.add(vi_name)

        # Process SubVIs using qualified names from metadata
        # ALWAYS record dependencies, only load SubVI files when expand_subvis=True
        if main_xml and main_xml.exists():
            # Use path refs from metadata (already parsed)
            subvi_ref_map = {
                ref.qualified_name: ref
                for ref in metadata.subvi_path_refs
                if ref.qualified_name
            }

            # Caller directory for relative path resolution
            caller_dir = bd_xml.parent

            # Use subvi_qualified_names from VIVI entries (authoritative source)
            for subvi_qname in metadata.subvi_qualified_names:
                # Self-call check: exact qualified name match
                if subvi_qname == vi_name:
                    # Skip self-calls to prevent infinite recursion
                    continue

                # Also check visited set (for other VIs in the call chain)
                if subvi_qname in visited:
                    continue

                ref = subvi_ref_map.get(subvi_qname)
                # Use relative path from ref if available for better resolution
                if ref and ref.path_tokens:
                    lookup_path = ref.get_relative_path()
                    is_vilib = ref.is_vilib
                    is_userlib = ref.is_userlib
                else:
                    # Fall back to extracting filename from qualified name
                    if ":" in subvi_qname:
                        lookup_path = subvi_qname.split(":")[-1]
                    else:
                        lookup_path = subvi_qname
                    is_vilib = False
                    is_userlib = False

                if expand_subvis:
                    # Try to find and load the SubVI
                    subvi_path = self._find_subvi(
                        lookup_path, search_paths, caller_dir, is_vilib, is_userlib
                    )
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
                            self._stubs.add(subvi_qname)
                            self._dep_graph.add_node(subvi_qname)
                            self._dep_graph.add_edge(vi_name, subvi_qname)
                    else:
                        # Mark as stub (file not found)
                        self._stubs.add(subvi_qname)
                        self._dep_graph.add_node(subvi_qname)
                        self._dep_graph.add_edge(vi_name, subvi_qname)
                else:
                    # Not expanding - record dependency as stub
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
        """Find a SubVI file in search paths.

        Args:
            vi_path: VI name or relative path (e.g., "Utilities/MyVI.vi")
            search_paths: Directories to search
            caller_dir: Directory of the calling VI for relative resolution
            is_vilib: True if SubVI is from <vilib>
            is_userlib: True if SubVI is from <userlib>
        """
        vi_name = Path(vi_path).name
        path_parts = Path(vi_path).parts

        # For same-directory VIs (not vilib/userlib), try caller's directory first
        if caller_dir and not is_vilib and not is_userlib:
            # Try exact match in caller's directory
            candidate = caller_dir / vi_name
            if candidate.exists():
                return candidate

            # Try relative path from caller's parent directories
            if len(path_parts) > 1:
                for parent in [caller_dir] + list(caller_dir.parents)[:3]:
                    candidate = parent / vi_path
                    if candidate.exists():
                        return candidate

        for search_path in search_paths:
            # Try with full relative path first
            if len(path_parts) > 1:
                candidate = search_path / vi_path
                if candidate.exists():
                    return candidate

            # Direct path with just filename
            candidate = search_path / vi_name
            if candidate.exists():
                return candidate

            # Recursive search by filename
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
                if not subvi_name:
                    continue
                subvi_name = self.resolve_vi_name(subvi_name)
                if subvi_name not in self._dataflow:
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

    @staticmethod
    def _format_lv_type_for_display(lv_type: LVType) -> str:
        """Format LVType for human-readable display.

        Args:
            lv_type: The LVType to format

        Returns:
            Human-readable type string
        """
        if lv_type.kind == "primitive":
            return lv_type.underlying_type or "Any"
        elif lv_type.kind == "enum":
            if lv_type.typedef_name:
                # Extract just the filename from qualified name
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

    def _build_dataflow_graph(
        self,
        bd: BlockDiagram,
        fp: FrontPanel | None,
        conpane: ConnectorPane | None,
        wiring_rules: dict[int, int],
        vi_name: str,
        type_map: dict[int, LVType] | None = None,
    ) -> nx.DiGraph:
        """Build a dataflow graph from a BlockDiagram.

        Nodes: operations, constants, FP terminals (inputs/outputs)
        Edges: wires (data connections)

        Only includes FP terminals that are on the connector pane (public interface).
        """
        if type_map is None:
            type_map = {}
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

            # Get type from parser (already resolved TypeID → ParsedType)
            # Then enrich with vilib_resolver data
            lv_type = None
            control_type_str = ctrl.control_type if ctrl else None

            # PRIMARY: Get parsed_type from block diagram terminal info
            term_info = bd.terminal_info.get(fp_term.uid)
            if term_info and term_info.parsed_type:
                lv_type = self._enrich_type(term_info.parsed_type)

            # FALLBACK: Try control_type (but this doesn't have typedef info)
            if not lv_type and control_type_str:
                lv_type = control_type_to_lvtype(control_type_str)

            node_attrs: dict[str, Any] = {
                "kind": kind,
                "name": ctrl.name if ctrl else fp_term.name,
                "is_indicator": fp_term.is_indicator,
                "is_public": is_public,  # On connector pane = public interface
                "slot_index": slot_index,  # Position on connector pane
                "wiring_rule": wiring_rule,  # 0=Invalid, 1=Required, 2=Rec, 3=Opt
                "control_type": ctrl.control_type if ctrl else None,
                "default_value": ctrl.default_value if ctrl else None,
                "enum_values": ctrl.enum_values if ctrl else [],
            }
            if lv_type:
                node_attrs["lv_type"] = lv_type
                node_attrs["type"] = self._format_lv_type_for_display(lv_type)
                if lv_type.typedef_path:
                    node_attrs["typedef_path"] = lv_type.typedef_path
                if lv_type.typedef_name:
                    node_attrs["typedef_name"] = lv_type.typedef_name
            else:
                node_attrs["type"] = "Any"

            g.add_node(fp_term.uid, **node_attrs)

        # Add constants WITH DECODED VALUES
        for const in bd.constants:
            val_type, decoded_value = decode_constant(const)

            # Get parsed_type from parser and enrich it
            lv_type = None
            term_info = bd.terminal_info.get(const.uid)
            if term_info and term_info.parsed_type:
                lv_type = self._enrich_type(term_info.parsed_type)

            node_attrs = {
                "kind": "constant",
                "value": decoded_value,
                "type": val_type,
                "raw_value": const.value,
                "label": const.label,
            }
            if lv_type:
                node_attrs["lv_type"] = lv_type

            g.add_node(const.uid, **node_attrs)

        # Add operations (SubVIs and primitives)
        # dynIUse = dynamic dispatch VI (class method calls)
        for node in bd.nodes:
            if node.node_type in ("iUse", "polyIUse", "dynIUse"):
                node_kind = "subvi"
            elif isinstance(node, PrimitiveNode):
                node_kind = "primitive"
            elif node.node_type in ("caseStruct", "select"):
                node_kind = "caseStruct"
            elif node.node_type in ("whileLoop", "forLoop"):
                node_kind = "loop"
            else:
                node_kind = "operation"

            # Collect terminals for this operation
            terminals = []
            for term_uid, term_info in bd.terminal_info.items():
                if term_info.parent_uid == node.uid:
                    # Get parsed_type and enrich it
                    lv_type = None
                    if term_info.parsed_type:
                        lv_type = self._enrich_type(term_info.parsed_type)

                    term_dict: dict[str, Any] = {
                        "id": term_uid,
                        "index": term_info.index,
                        "type": lv_type.underlying_type if lv_type else "Any",
                        "name": term_info.name,
                        "direction": "output" if term_info.is_output else "input",
                        "lv_type": lv_type,
                    }
                    if lv_type and lv_type.typedef_path:
                        term_dict["typedef_path"] = lv_type.typedef_path
                    if lv_type and lv_type.typedef_name:
                        term_dict["typedef_name"] = lv_type.typedef_name
                    terminals.append(term_dict)

            # Resolve primitive name from registry
            node_name = node.name
            if isinstance(node, PrimitiveNode) and node.prim_res_id:
                # Always resolve - parser may set generic name like "Primitive"
                resolved = resolve_primitive(prim_id=node.prim_res_id)
                if resolved:
                    node_name = resolved.name

            # Map node_type to friendly name from primitive registry
            if not node_name and node.node_type:
                resolved = get_prim_resolver().resolve_by_node_type(node.node_type)
                if resolved:
                    node_name = resolved.name

            # Get description for SubVIs from vilib
            description = None
            if node_kind == "subvi" and node_name:
                from .vilib_resolver import get_resolver
                resolver = get_resolver()
                vi_entry = resolver.resolve_by_name(node_name)
                if vi_entry and vi_entry.description:
                    description = vi_entry.description

            node_attrs: dict[str, Any] = {
                "kind": node_kind,
                "name": node_name,
                "node_type": node.node_type,
                "terminals": sorted(terminals, key=lambda t: t.get("index", 0)),
            }
            # Add primitive-specific fields
            if isinstance(node, PrimitiveNode):
                node_attrs["prim_id"] = node.prim_res_id
                node_attrs["prim_index"] = node.prim_index
            # Add cpdArith-specific fields
            if isinstance(node, CpdArithNode):
                node_attrs["operation"] = node.operation
            # Add property node fields
            if isinstance(node, PropertyNode):
                node_attrs["object_name"] = node.object_name
                node_attrs["object_method_id"] = node.object_method_id
                node_attrs["properties"] = node.properties
            # Add invoke node fields
            if isinstance(node, InvokeNode):
                node_attrs["object_name"] = node.object_name
                node_attrs["object_method_id"] = node.object_method_id
                node_attrs["method_name"] = node.method_name
                node_attrs["method_code"] = node.method_code
            # Add polymorphic variant name
            if isinstance(node, SubVINode) and node.poly_variant_name:
                node_attrs["poly_variant_name"] = node.poly_variant_name
            if description:
                node_attrs["description"] = description

            g.add_node(node.uid, **node_attrs)

        # Add terminal nodes (for wire routing)
        for term_uid, term_info in bd.terminal_info.items():
            if term_uid not in g:  # Don't override FP terminals
                # Get parsed_type and enrich it
                lv_type = None
                if term_info.parsed_type:
                    lv_type = self._enrich_type(term_info.parsed_type)

                node_attrs: dict[str, Any] = {
                    "kind": "terminal",
                    "parent_id": term_info.parent_uid,
                    "index": term_info.index,
                    "type": lv_type.underlying_type if lv_type else "Any",
                    "name": term_info.name,
                    "direction": "output" if term_info.is_output else "input",
                    "lv_type": lv_type,
                }
                if lv_type and lv_type.typedef_path:
                    node_attrs["typedef_path"] = lv_type.typedef_path
                if lv_type and lv_type.typedef_name:
                    node_attrs["typedef_name"] = lv_type.typedef_name
                g.add_node(term_uid, **node_attrs)

        # Ensure all terminal parent nodes exist in the graph.
        # sRN (shift register) nodes have terminals in terminal_info but
        # aren't in bd.nodes — they're structural infrastructure, not
        # operations. We add them as "infrastructure" so wire resolution
        # can trace through them, but the codegen skips them.
        for term_uid, term_info in bd.terminal_info.items():
            parent_uid = term_info.parent_uid
            if parent_uid and parent_uid not in g:
                g.add_node(parent_uid, kind="infrastructure", node_type="sRN")

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

                # lSR, lpTun, caseSel: data flows INTO the loop/structure
                # outer -> inner
                if tunnel.tunnel_type in ("lSR", "lpTun", "caseSel"):
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

        # Register case structure tunnel terminals as graph nodes
        # (same pattern as flat sequences below — ensures wire endpoints exist)
        for case_struct in bd.case_structures:
            for tunnel in case_struct.tunnels:
                for uid in (tunnel.outer_terminal_uid, tunnel.inner_terminal_uid):
                    if uid and uid not in g.nodes:
                        g.add_node(
                            uid,
                            kind="terminal",
                            parent_id=case_struct.uid,
                        )

        # Register loop tunnel terminals as graph nodes
        for loop in bd.loops:
            for tunnel in loop.tunnels:
                for uid in (tunnel.outer_terminal_uid, tunnel.inner_terminal_uid):
                    if uid and uid not in g.nodes:
                        g.add_node(
                            uid,
                            kind="terminal",
                            parent_id=loop.uid,
                        )

        # Add tunnel edges from flat sequence structures
        for flat_seq in bd.flat_sequences:
            # Register outer tunnel terminals as belonging to the
            # flat sequence so topological sort creates proper deps
            for tunnel in flat_seq.tunnels:
                outer_uid = tunnel.outer_terminal_uid
                inner_uid = tunnel.inner_terminal_uid
                # Ensure outer terminal has parent_id = flat_seq
                if outer_uid in g.nodes:
                    g.nodes[outer_uid]["parent_id"] = flat_seq.uid
                    g.nodes[outer_uid]["kind"] = "terminal"
                else:
                    g.add_node(
                        outer_uid,
                        kind="terminal",
                        parent_id=flat_seq.uid,
                    )

                outer_parent = bd.terminal_info.get(outer_uid)
                inner_parent = bd.terminal_info.get(inner_uid)
                outer_pid = (
                    outer_parent.parent_uid if outer_parent
                    else flat_seq.uid
                )
                inner_pid = (
                    inner_parent.parent_uid if inner_parent
                    else flat_seq.uid
                )

                # Both seqTun and flatSeqTun: outer -> inner
                g.add_edge(
                    outer_uid,
                    inner_uid,
                    tunnel_type=tunnel.tunnel_type,
                    seq_uid=flat_seq.uid,
                    from_parent=outer_pid,
                    to_parent=inner_pid,
                )

        return g

    # === Dependency Graph Queries ===

    def resolve_vi_name(self, vi_name: str) -> str:
        """Resolve a VI name to its canonical form.

        Handles both qualified names (MyLib.lvlib:VI.vi) and simple filenames.
        Returns the name as stored in the graph.
        """
        # Direct match
        if vi_name in self._dataflow:
            return vi_name
        # Check if it's a filename that maps to a qualified name
        if vi_name in self._qualified_aliases:
            return self._qualified_aliases[vi_name]
        # Check if it's a qualified name where we only have filename
        # (strip library prefix and try)
        if ":" in vi_name:
            simple_name = vi_name.split(":")[-1]
            if simple_name in self._dataflow:
                return simple_name
        return vi_name  # Return as-is, let caller handle not-found

    def list_vis(self) -> list[str]:
        """List all VIs in the graph (excluding stubs)."""
        return list(self._dataflow.keys())

    def get_vi_source_path(self, vi_name: str) -> Path | None:
        """Get the source file path for a VI.

        Args:
            vi_name: Qualified VI name (e.g., "Library.lvlib:VI.vi")

        Returns:
            Path to the original .vi file, or None if not available
        """
        return self._source_paths.get(vi_name)

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
        # Use lexicographical sort for deterministic ordering
        # Key function uses minimum VI name in each SCC for stable ordering
        def scc_key(scc_id: int) -> str:
            return min(condensation.nodes[scc_id]["members"])

        scc_order = list(reversed(list(
            nx.lexicographical_topological_sort(condensation, key=scc_key)
        )))

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
    ) -> list[FPTerminalNode]:
        """Get VI input terminals.

        Args:
            vi_name: Name of the VI
            public_only: If True, only return connector pane inputs (default).
                        If False, include internal controls too.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        results = []
        for n, d in g.nodes(data=True):
            if d.get("kind") != "input":
                continue
            if public_only and not d.get("is_public", True):
                continue
            results.append(FPTerminalNode(
                id=n,
                kind="input",
                name=d.get("name"),
                is_indicator=d.get("is_indicator", False),
                is_public=d.get("is_public", True),
                slot_index=d.get("slot_index"),
                wiring_rule=d.get("wiring_rule", 0),
                type_desc=d.get("type_desc"),
                control_type=d.get("control_type"),
                default_value=d.get("default_value"),
                enum_values=d.get("enum_values", []),
                type=d.get("type"),
                lv_type=d.get("lv_type"),
            ))
        return results

    def get_outputs(
        self, vi_name: str, *, public_only: bool = True
    ) -> list[FPTerminalNode]:
        """Get VI output terminals.

        Args:
            vi_name: Name of the VI
            public_only: If True, only return connector pane outputs (default).
                        If False, include internal indicators too.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        results = []
        for n, d in g.nodes(data=True):
            if d.get("kind") != "output":
                continue
            if public_only and not d.get("is_public", True):
                continue
            results.append(FPTerminalNode(
                id=n,
                kind="output",
                name=d.get("name"),
                is_indicator=d.get("is_indicator", True),
                is_public=d.get("is_public", True),
                slot_index=d.get("slot_index"),
                wiring_rule=d.get("wiring_rule", 0),
                type_desc=d.get("type_desc"),
                control_type=d.get("control_type"),
                default_value=d.get("default_value"),
                enum_values=d.get("enum_values", []),
                type=d.get("type"),
                lv_type=d.get("lv_type"),
            ))
        return results

    def get_constants(self, vi_name: str) -> list[Constant]:
        """Get all constants in a VI."""
        g = self._dataflow.get(vi_name)
        if g is None:
            return []
        results = []
        for n, d in g.nodes(data=True):
            if d.get("kind") != "constant":
                continue
            results.append(Constant(
                id=n,
                value=d.get("value"),
                lv_type=d.get("lv_type"),  # LVType from parsing
                raw_value=d.get("raw_value"),
                name=d.get("label"),  # Graph stores as "label", Constant uses "name"
            ))
        return results

    def get_operations(self, vi_name: str) -> list[Operation]:
        """Get all operations (SubVIs, primitives) in a VI.

        Returns operations in dataflow execution order.
        Only returns top-level operations - inner loop operations are nested in
        the loop's inner_nodes list.
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []

        # Collect all inner node UIDs from loops - these should NOT appear at top level
        inner_node_uids: set[str] = set()
        for loop_struct in self._loop_structures.get(vi_name, {}).values():
            inner_node_uids.update(loop_struct.inner_node_uids)

        # Also collect inner node UIDs from case structures
        for case_struct in self._case_structures.get(vi_name, {}).values():
            for frame in case_struct.frames:
                inner_node_uids.update(frame.inner_node_uids)

        # Also collect inner node UIDs from flat sequences
        for flat_seq in self._flat_sequences.get(vi_name, {}).values():
            for frame in flat_seq.frames:
                inner_node_uids.update(frame.inner_node_uids)

        # Get operations in dataflow order, excluding inner loop/case nodes
        ordered_ids = [
            uid for uid in self.get_operation_order(vi_name)
            if uid not in inner_node_uids
        ]
        op_set = set(ordered_ids)

        # Add any ops not in the sorted order (disconnected), excluding inner nodes
        for n, d in g.nodes(data=True):
            if (d.get("kind") in _OPERATION_KINDS
                and n not in op_set
                and n not in inner_node_uids):
                ordered_ids.append(n)

        return [
            self._build_operation(n, g, vi_name)
            for n in ordered_ids
            if n in g.nodes
        ]

    def _build_operation(
        self, uid: str, g: nx.DiGraph, vi_name: str
    ) -> Operation:
        """Build a single Operation dataclass from a graph node.

        This is the ONE place that constructs Operation objects. Both
        get_operations() (top-level) and _build_inner_nodes() (nested)
        delegate here so all operations get identical processing:
        terminal enrichment, structure handling, name resolution.
        """
        d = dict(g.nodes[uid])
        kind = d.get("kind", "operation")
        node_type = d.get("node_type", "")

        labels = _get_operation_labels(kind)

        # Enrich terminals with callee parameter names for SubVIs
        raw_terminals = d.get("terminals", [])
        if kind == "subvi":
            subvi_name = d.get("name")
            raw_terminals = self._enrich_subvi_terminals(
                raw_terminals, subvi_name, vi_name
            )

        terminals = self._to_terminal_list(raw_terminals)

        # Structure-specific fields
        tunnels: list[Tunnel] = []
        inner_nodes: list[Operation] = []
        loop_type: str | None = None
        stop_cond: str | None = None
        case_frames: list[CaseFrame] = []
        selector_terminal: str | None = None

        if node_type in ("whileLoop", "forLoop"):
            labels = ["Loop"]
            loop_type = node_type
            loop_struct = self._loop_structures.get(vi_name, {}).get(uid)
            if loop_struct:
                tunnels = self._build_tunnels(loop_struct.tunnels)
                inner_nodes = self._build_inner_nodes(
                    loop_struct.inner_node_uids, g, vi_name
                )
                stop_cond = loop_struct.stop_condition_terminal_uid

        elif node_type in ("caseStruct", "select"):
            labels = ["CaseStructure"]
            case_struct = self._case_structures.get(vi_name, {}).get(uid)
            if case_struct:
                tunnels = self._build_tunnels(case_struct.tunnels)
                selector_terminal = case_struct.selector_terminal_uid
                case_frames = self._build_case_frames(
                    case_struct.frames, g, vi_name
                )

        elif node_type in ("flatSequence", "seq"):
            labels = ["FlatSequence"]
            flat_seq = self._flat_sequences.get(vi_name, {}).get(uid)
            if flat_seq:
                tunnels = self._build_tunnels(flat_seq.tunnels)
                case_frames = self._build_sequence_frames(
                    flat_seq.frames, g, vi_name
                )
                terminals = self._build_tunnel_terminals(
                    flat_seq.tunnels, g,
                )

        # Name fallback for unnamed structures
        node_name = d.get("name")
        if not node_name and node_type:
            node_name = _NODE_TYPE_NAMES.get(node_type)

        return Operation(
            id=uid,
            name=node_name,
            labels=labels,
            primResID=d.get("prim_id"),
            terminals=terminals,
            node_type=node_type or None,
            loop_type=loop_type,
            tunnels=tunnels,
            inner_nodes=inner_nodes,
            stop_condition_terminal=stop_cond,
            description=d.get("description"),
            operation=d.get("operation"),
            object_name=d.get("object_name"),
            object_method_id=d.get("object_method_id"),
            properties=d.get("properties", []),
            method_name=d.get("method_name"),
            method_code=d.get("method_code"),
            case_frames=case_frames,
            selector_terminal=selector_terminal,
            poly_variant_name=d.get("poly_variant_name"),
        )

    @staticmethod
    def _build_tunnels(parser_tunnels: list) -> list[Tunnel]:
        """Convert parser tunnel objects to Tunnel dataclasses."""
        return [
            Tunnel(
                outer_terminal_uid=t.outer_terminal_uid,
                inner_terminal_uid=t.inner_terminal_uid,
                tunnel_type=t.tunnel_type,
                paired_terminal_uid=t.paired_terminal_uid,
            )
            for t in parser_tunnels
        ]

    def _build_tunnel_terminals(
        self,
        tunnels: list,
        g: nx.DiGraph,
    ) -> list[Terminal]:
        """Build Terminal list from tunnel outer UIDs.

        Used for flat sequences where the flatSequence XML element
        has no termList — terminals live on the sequenceFrame children.
        We synthesize terminals from tunnel outer UIDs so the codegen
        topological sort can see data dependencies.
        """
        seen: set[str] = set()
        terminals: list[Terminal] = []
        for i, tunnel in enumerate(tunnels):
            outer = tunnel.outer_terminal_uid
            if outer in seen:
                continue
            seen.add(outer)
            # Determine direction from graph edges:
            # If outer terminal is a source for any non-tunnel edge → output
            # If outer terminal is a destination → input
            is_output = False
            if g.has_node(outer):
                for _, dest, edata in g.out_edges(outer, data=True):
                    if not edata.get("tunnel_type"):
                        is_output = True
                        break
            direction = "output" if is_output else "input"
            terminals.append(Terminal(
                id=outer,
                index=i,
                direction=direction,
            ))
        return terminals

    def _to_terminal_list(self, raw_terminals: list[dict]) -> list[Terminal]:
        """Convert list of terminal dicts to Terminal dataclasses."""
        terminals = []
        for t in raw_terminals:
            terminals.append(Terminal(
                id=t.get("id", ""),
                index=t.get("index", 0),
                direction=t.get("direction", "input"),
                type=t.get("type", "Any"),
                name=t.get("name"),
                typedef_path=t.get("typedef_path"),
                typedef_name=t.get("typedef_name"),
                callee_param_name=t.get("callee_param_name"),
                lv_type=t.get("lv_type"),
            ))
        return terminals

    def _enrich_subvi_terminals(
        self,
        terminals: list[dict[str, Any]],
        subvi_name: str | None,
        caller_vi: str,
    ) -> list[dict[str, Any]]:
        """Add callee parameter names to SubVI terminals.

        Uses cross-VI bindings to map caller terminal index → callee FP terminal name.
        """
        if not subvi_name:
            return terminals
        subvi_name = self.resolve_vi_name(subvi_name)
        if subvi_name not in self._dataflow:
            return terminals

        # Get callee FP terminals with slot indices
        subvi_g = self._dataflow[subvi_name]
        slot_to_name: dict[int, str] = {}
        for term_id, term_data in subvi_g.nodes(data=True):
            slot = term_data.get("slot_index")
            name = term_data.get("name")
            if slot is not None and name and term_data.get("kind") in (
                "input",
                "output",
            ):
                slot_to_name[slot] = name

        # Enrich terminals with callee parameter names
        enriched = []
        for term in terminals:
            term_copy = dict(term)
            term_index = term.get("index")
            if term_index is not None and term_index in slot_to_name:
                term_copy["callee_param_name"] = slot_to_name[term_index]
            enriched.append(term_copy)

        return enriched

    def _sort_inner_uids(
        self, uids: list[str], g: nx.DiGraph
    ) -> list[str]:
        """Topologically sort inner node UIDs by their wire dependencies.

        Inner nodes in case frames / loops may be listed in XML order,
        which doesn't respect data dependencies. Sort them so producers
        execute before consumers.

        Only considers real operation nodes (not sRN infrastructure) to
        avoid false dependency cycles from tunnel terminal wiring.
        """
        uid_set = set(uids)
        if len(uid_set) <= 1:
            return list(uids)

        # Filter to real operations only (exclude sRN and other infra)
        op_uid_set: set[str] = set()
        for uid in uid_set:
            if uid in g.nodes:
                kind = g.nodes[uid].get("kind")
                if kind in _OPERATION_KINDS:
                    op_uid_set.add(uid)

        if len(op_uid_set) <= 1:
            return list(uids)

        # Build terminal → parent mapping for operation nodes only
        terminal_to_op: dict[str, str] = {}
        for n, d in g.nodes(data=True):
            if d.get("kind") == "terminal":
                parent = d.get("parent_id")
                if parent in op_uid_set:
                    terminal_to_op[n] = parent

        # Build dependency graph among inner operation nodes
        dep = nx.DiGraph()
        dep.add_nodes_from(op_uid_set)
        for u, v, _ in g.edges(data=True):
            src_op = terminal_to_op.get(u)
            dst_op = terminal_to_op.get(v)
            if src_op in op_uid_set and dst_op in op_uid_set and src_op != dst_op:
                dep.add_edge(src_op, dst_op)

        try:
            sorted_ops = list(nx.topological_sort(dep))
        except nx.NetworkXUnfeasible:
            sorted_ops = list(op_uid_set)

        # Build final list: sorted ops first, then non-op uids in original order
        sorted_set = set(sorted_ops)
        result = list(sorted_ops)
        for uid in uids:
            if uid not in sorted_set:
                result.append(uid)

        return result

    def _build_inner_nodes(
        self, uids: list[str], g: nx.DiGraph, vi_name: str
    ) -> list[Operation]:
        """Build Operation dataclasses for nodes inside a loop.

        Recursively handles nested loops.
        """
        sorted_uids = self._sort_inner_uids(uids, g)
        return [
            self._build_operation(uid, g, vi_name)
            for uid in sorted_uids
            if uid in g.nodes and g.nodes[uid].get("kind") in _OPERATION_KINDS
        ]

    def _build_case_frames(
        self,
        parser_frames: list,  # list[parser.models.CaseFrame]
        g: nx.DiGraph,
        vi_name: str,
    ) -> list[CaseFrame]:
        """Build CaseFrame dataclasses from parser case frames.

        Converts parser CaseFrame (UIDs) to graph_types CaseFrame (Operations).
        """
        result_frames: list[CaseFrame] = []

        for parser_frame in parser_frames:
            # Build operations for this frame's inner nodes
            frame_ops = self._build_inner_nodes(
                parser_frame.inner_node_uids, g, vi_name
            )

            result_frames.append(CaseFrame(
                selector_value=parser_frame.selector_value,
                inner_node_uids=parser_frame.inner_node_uids,
                operations=frame_ops,
                is_default=parser_frame.is_default,
            ))

        return result_frames

    def _build_sequence_frames(
        self,
        parser_frames: list,  # list[parser.models.SequenceFrame]
        g: nx.DiGraph,
        vi_name: str,
    ) -> list[CaseFrame]:
        """Build CaseFrame dataclasses from sequence frames.

        Reuses CaseFrame with selector_value as frame index ("0", "1", "2").
        Codegen treats these as sequential (not conditional).
        """
        result_frames: list[CaseFrame] = []

        for idx, parser_frame in enumerate(parser_frames):
            frame_ops = self._build_inner_nodes(
                parser_frame.inner_node_uids, g, vi_name
            )

            result_frames.append(CaseFrame(
                selector_value=str(idx),
                inner_node_uids=parser_frame.inner_node_uids,
                operations=frame_ops,
            ))

        return result_frames

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
            if d.get("kind") in _OPERATION_KINDS
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

    def get_wires(self, vi_name: str) -> list[Wire]:
        """Get all wires (edges) in a VI's dataflow graph."""
        g = self._dataflow.get(vi_name)
        if g is None:
            return []

        # Process tunnel edges first, then normal edges, so normal edges
        # take priority in the flow map (last write wins for same destination).
        tunnel_edges = []
        normal_edges = []
        for u, v, d in g.edges(data=True):
            if d.get("tunnel_type"):
                tunnel_edges.append((u, v, d))
            else:
                normal_edges.append((u, v, d))

        result = []
        for u, v, d in tunnel_edges + normal_edges:
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

            # Look up slot_index from terminal node data (for SubVI param lookup)
            # Prefer slot_index (connector pane), fall back to terminal index
            from_term_data = g.nodes.get(u, {})
            to_term_data = g.nodes.get(v, {})
            from_slot_index = (
                from_term_data.get("slot_index") or from_term_data.get("index")
            )
            to_slot_index = (
                to_term_data.get("slot_index") or to_term_data.get("index")
            )

            result.append(Wire(
                from_terminal_id=u,
                to_terminal_id=v,
                from_parent_id=from_parent_id,
                to_parent_id=to_parent_id,
                from_parent_name=from_node.get("name"),
                to_parent_name=to_node.get("name"),
                from_parent_labels=from_labels,
                to_parent_labels=to_labels,
                from_slot_index=from_slot_index,
                to_slot_index=to_slot_index,
            ))
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
        Returns dataclass instances for use with new AST-based codegen.
        """
        # Resolve to canonical name (handles qualified names and aliases)
        vi_name = self.resolve_vi_name(vi_name)
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

        # Get dataclasses (keep as dataclasses for new codegen)
        inputs = list(self.get_inputs(vi_name))
        outputs = list(self.get_outputs(vi_name))
        constants = list(self.get_constants(vi_name))
        operations = list(self.get_operations(vi_name))
        data_flow = list(self.get_wires(vi_name))

        # Get library/class metadata
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
        """Get all polymorphic VIs and their variants.

        Returns dict mapping wrapper VI name to list of variant VI names.
        Based on actual VI metadata, not naming heuristics.
        """
        return {
            vi_name: info["variants"]
            for vi_name, info in self._poly_info.items()
            if info.get("variants")
        }

    def get_poly_variant_wrappers(self) -> dict[str, str]:
        """Get mapping of variant VI names to their wrapper VI.

        Inverts the polymorphic groups for quick variant->wrapper lookup.
        """
        result: dict[str, str] = {}
        for wrapper, variants in self._poly_info.items():
            for variant in variants.get("variants", []):
                result[variant] = wrapper
        return result

    # === Parallel Branch Detection ===

    def find_branch_points(self, vi_name: str) -> list[BranchPoint]:
        """Find terminals where one output feeds multiple inputs.

        These are fork points in the dataflow graph where parallel
        execution branches begin. Used for error handling to isolate
        exceptions in each branch.

        Args:
            vi_name: Name of the VI to analyze

        Returns:
            List of BranchPoint objects describing fork points
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return []

        branch_points: list[BranchPoint] = []

        # Find terminals with multiple outgoing edges
        for node_id in g.nodes():
            successors = list(g.successors(node_id))
            if len(successors) > 1:
                # This is a branch point - one output feeds multiple inputs
                node_data = g.nodes.get(node_id, {})

                # Get the parent operation if this is a terminal
                source_op = None
                if node_data.get("kind") == "terminal":
                    source_op = node_data.get("parent_id")
                elif node_data.get("kind") in ("subvi", "primitive", "operation"):
                    source_op = node_id

                branch_points.append(BranchPoint(
                    source_terminal=node_id,
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
        """Trace a single branch from a start terminal to its merge point.

        Follows the dataflow from the start terminal until either:
        1. Reaching a VI output terminal
        2. Reaching a node that receives input from another branch
           (merge point)
        3. Reaching a node with no successors

        Args:
            vi_name: Name of the VI
            start_terminal: Terminal ID where this branch starts
            all_branch_starts: Set of all branch start terminals (to detect merges)

        Returns:
            ParallelBranch describing this branch's operations and merge point
        """
        g = self._dataflow.get(vi_name)
        if g is None:
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

        def trace(terminal_id: str) -> bool:
            """Trace from terminal, collecting operations.

            Returns True if we found a merge point, False otherwise.
            """
            nonlocal merge_terminal, merge_operation

            if terminal_id in visited:
                return False
            visited.add(terminal_id)

            node_data = g.nodes.get(terminal_id, {})
            kind = node_data.get("kind", "")

            # If we hit an output terminal, branch ends at VI boundary
            if kind == "output":
                merge_terminal = terminal_id
                return True

            # If this is an operation, collect it
            if kind in ("subvi", "primitive", "operation"):
                operations.append(terminal_id)

            # Check successors
            successors = list(g.successors(terminal_id))

            for succ in successors:
                succ_data = g.nodes.get(succ, {})

                # Check if this successor is fed by another branch (merge point)
                predecessors = list(g.predecessors(succ))
                other_inputs = [
                    p for p in predecessors
                    if p != terminal_id and p in all_branch_starts
                ]
                if other_inputs:
                    # This is a merge point - stop here
                    merge_terminal = succ
                    if succ_data.get("kind") == "terminal":
                        merge_operation = succ_data.get("parent_id")
                    elif succ_data.get("kind") in ("subvi", "primitive", "operation"):
                        merge_operation = succ
                    return True

                # Continue tracing
                if trace(succ):
                    return True

            return False

        trace(start_terminal)

        return ParallelBranch(
            branch_id=0,  # Will be set by caller
            source_terminal=start_terminal,
            operation_ids=operations,
            merge_terminal=merge_terminal,
            merge_operation=merge_operation,
        )

    def get_parallel_branches(
        self, vi_name: str
    ) -> list[tuple[BranchPoint, list[ParallelBranch]]]:
        """Get all parallel branch structures in a VI.

        Finds branch points and traces each branch to its merge point.
        Returns a list of (BranchPoint, [ParallelBranch, ...]) tuples.

        Args:
            vi_name: Name of the VI to analyze

        Returns:
            List of (branch_point, branches) tuples
        """
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
        """Check if a VI has any parallel branch points.

        Quick check without full branch tracing - useful for deciding
        whether to enable the held error model.

        Args:
            vi_name: Name of the VI to check

        Returns:
            True if VI has at least one branch point
        """
        g = self._dataflow.get(vi_name)
        if g is None:
            return False

        for node_id in g.nodes():
            successors = list(g.successors(node_id))
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

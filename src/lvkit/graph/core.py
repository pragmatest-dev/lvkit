"""Core InMemoryVIGraph class definition and shared utilities.

Contains __init__, clear, _qid, _enrich_type, context manager, connect(),
set_var_name, get_var_name, incoming_edges, outgoing_edges, terminal_is_wired,
and module-level helper functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from ..load_mode import LoadMode
from ..models import ClusterField, LVType, LVTypeKind
from ..parser.models import ParsedType, ParsedVI

if TYPE_CHECKING:
    from ..parser.layout import Layout
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .models import (
    AnyGraphNode,
    ConstantNode,
    DisableStructureNode,
    EventStructureNode,
    LabelNode,
    LocalVariableNode,
    PolyInfo,
    StructureNode,
    VIHealth,
    VIMetadata,
    VINode,
    VIProperties,
    WireEnd,
)
from .models import (
    FormulaNode as GraphFormulaNode,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
)

# Map operation kind (the coarse op-kind string from _graph_node_to_op_kind)
# to its human-readable display label. Distinct key space from
# parser.node_types.get_display_name, which maps raw parser node_type tokens
# (e.g. "whileLoop", "forLoop") — do not try to merge the two.
_KIND_TO_TAGS: dict[str, str] = {
    "vi": "SubVI",
    "primitive": "Primitive",
    "caseStruct": "CaseStructure",
    "loop": "Loop",
    "operation": "Operation",
    "constant": "Constant",
    "formula": "FormulaNode",
    "local_variable": "LocalVariable",
    "disableStruct": "DisableStructure",
    "eventStruct": "EventStructure",
    "flatSequence": "FlatSequence",
    "inPlaceStruct": "InPlaceStructure",
}


def kind_display(kind: str) -> str:
    """Human-readable display label for an operation kind string."""
    return _KIND_TO_TAGS.get(kind, kind)


# Graph node kinds that represent executable operations
_OPERATION_KINDS = (
    "vi",
    "primitive",
    "operation",
    "caseStruct",
    "loop",
    "formula",
    "disableStruct",
    "eventStruct",
    "flatSequence",
    "inPlaceStruct",
)


def _node_order_key(uid: str) -> tuple[str, int, str]:
    """Deterministic ordering key for node UIDs.

    Node UIDs are stored in per-VI sets whose iteration order is
    hash-randomized between processes. Sorting by this key — VI base, then
    numeric LabVIEW object id — gives a stable, natural order so codegen and
    diffs are byte-for-byte reproducible. Independent (parallel) operations
    left tied by the topological sort are broken by this key rather than by
    set iteration order.
    """
    base, _, tail = uid.rpartition("::")
    return (base, int(tail), "") if tail.isdigit() else (uid, -1, uid)


def _uid_of(op_id: str) -> str:
    """Trailing UID from an op.id ('...run.vi::1065' -> '1065') — the stable,
    path-prefix-independent node identity shared by the netlist + diff layers."""
    return op_id.rsplit("::", 1)[-1]


def _graph_node_to_op_kind(node: AnyGraphNode) -> str:
    """Map a typed graph node to the operation kind string."""
    if isinstance(node, VINode):
        return "vi"
    if isinstance(node, GraphFormulaNode):
        return "formula"
    if isinstance(node, GraphPrimitiveNode):
        return "primitive"
    if isinstance(node, DisableStructureNode):
        return "disableStruct"
    if isinstance(node, EventStructureNode):
        return "eventStruct"
    if isinstance(node, StructureNode):
        if node.node_type in ("caseStruct", "select"):
            return "caseStruct"
        if node.node_type in ("whileLoop", "forLoop"):
            return "loop"
        if node.node_type in ("flatSequence", "seq", "sequence"):
            return "flatSequence"
        if node.node_type == "decomposeRecomposeStructure":
            return "inPlaceStruct"
        return "operation"
    if isinstance(node, ConstantNode):
        return "constant"
    if isinstance(node, LocalVariableNode):
        return "local_variable"
    if isinstance(node, LabelNode):
        return "label"
    return "operation"


# Import mixins - these define methods that InMemoryVIGraph inherits
from .analysis import AnalysisMixin  # noqa: E402
from .construction import ConstructionMixin  # noqa: E402
from .loading import LoadingMixin  # noqa: E402
from .operations import OperationsMixin  # noqa: E402
from .queries import QueryMixin  # noqa: E402


class InMemoryVIGraph(
    LoadingMixin,
    ConstructionMixin,
    QueryMixin,
    OperationsMixin,
    AnalysisMixin,
):
    """In-memory VI graph using a single unified NetworkX MultiDiGraph.

    Usage:
        graph = InMemoryVIGraph()
        graph.load_vi("/path/to/Main.vi", LoadMode.FULL)

        # Process VIs in dependency order (handles recursive VIs)
        for vi_group in graph.get_generation_order():
            for vi_name in vi_group:
                # Get operations (topologically ordered)
                for op in graph.get_operations(vi_name):
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
        # Polymorphic VI info
        self._poly_info: dict[str, PolyInfo] = {}
        # Qualified name aliases: "Lib.lvlib:VI.vi" -> "VI.vi" (for library VIs)
        self._qualified_aliases: dict[str, str] = {}
        # Reverse indexes for resolve_vi_name: display-name / qualified-name ->
        # [vi_key]. A vi_key is the canonical source-path string (the VI's
        # identity; see _load_vi_recursive). List-valued because genuine on-disk
        # duplicates -- the same VI copied into a build-output tree, or parallel
        # plugin trees -- share a name/qname but have DISTINCT path keys. Path is
        # the identity; name/qname are non-unique attributes we index for lookup.
        self._name_to_keys: dict[str, list[str]] = {}
        self._qname_to_keys: dict[str, list[str]] = {}
        # Track loaded VIs across multiple load_vi() calls to prevent re-parsing
        self._loaded_vis: set[str] = set()
        # Depth a VI's DEPENDENCIES were loaded at (NONE/MINIMAL/FULL). A VI
        # first seen as a leaf (a caller's SubVI, child NONE) records NONE here;
        # a later load at a higher depth UPGRADES it (progressive partial->full)
        # instead of being blocked by the already-loaded check — without this, a
        # VI leaf-loaded before its own turn in a directory walk never gets its
        # OWN call edges. Absent key == not yet dependency-loaded.
        self._dep_load_mode: dict[str, LoadMode] = {}
        # Source file paths: vi_name -> Path to original .vi file
        self._source_paths: dict[str, Path] = {}
        # vi_key -> qualified DISPLAY name (qname/basename). Display only, never a
        # lookup key — resolve_vi_name still returns the vi_key (path) identity.
        self._vi_display_names: dict[str, str] = {}
        # Search paths the graph was loaded with, retained so decoration-only
        # lookups (a SubVI's own _ICON.png) can locate a project-local .vi by
        # name even under a MINIMAL load that never walks the SubVI tree.
        self._search_paths: list[Path] = []
        self._vi_file_index: dict[str, Path] | None = None  # lazy name -> path
        # VI metadata (identity only -- library/qualified_name/owning_libraries/
        # description). Sibling name-keyed facet dicts below carry the rest.
        self._vi_metadata: dict[str, VIMetadata] = {}
        # VI Properties (Protection/Execution/Window/…) from <LVSR>, keyed by
        # vi_name -- a sibling facet to _vi_metadata, NOT a VIMetadata field
        # (see VIMetadata's docstring: identity only).
        self._vi_properties: dict[str, VIProperties] = {}
        # Compile-health (emergent state, not a user setting) -- a SIBLING
        # facet to _vi_properties, never nested inside it.
        self._vi_health: dict[str, VIHealth] = {}
        # Optional disk roots for <vilib> / <userlib> / <instrlib> path tokens
        self._vilib_root: Path | None = None
        self._userlib_root: Path | None = None
        self._instrlib_root: Path | None = None
        # Per-VI block-diagram geometry, populated only when load_vi(layout=True)
        # (rendering). Empty for codegen loads — geometry is decoded from the
        # same parse (see parse_vi layout=), never a second heap read.
        self._layouts: dict[str, Layout] = {}
        # Whether the current load should decode + retain geometry.
        self._want_layout: bool = False
        # Pre-parsed VIs (bd_xml path str -> ParsedVI), warmed by
        # load_directory's optional parallel pre-parse pass (see
        # graph/parallel_parse.py). None outside that call: _load_vi_recursive
        # falls back to its own parse_vi() call on any miss, so this is purely
        # an optional cache, never a correctness dependency.
        self._parse_cache: dict[str, ParsedVI] | None = None

    def set_library_roots(
        self,
        vilib_root: Path | None = None,
        userlib_root: Path | None = None,
        instrlib_root: Path | None = None,
    ) -> None:
        """Set disk roots for <vilib>, <userlib> and <instrlib> path tokens.

        Call before loading any VIs. When set, dependency refs with
        <vilib>, <userlib> or <instrlib> tokens resolve to real .vi files on
        disk instead of falling through to JSON-only lookup.

        Also feeds the same roots to the extraction cache so a VI's cache
        namespace is chosen by prefix-matching its resolved path against these
        real roots (shared vs. per-project), not by substring-scanning the path.
        This is the single wiring point for all entry points (CLI, MCP,
        pipeline, render, docs), which each call this before ``load_vi``.
        """
        self._vilib_root = vilib_root
        self._userlib_root = userlib_root
        self._instrlib_root = instrlib_root
        # The run's library roots feed the cache classifier; set them on the
        # stdlib-only cache_paths module (no pylabview pulled in, no cycle).
        from lvkit import cache_paths

        cache_paths.set_extraction_roots(
            vilib_root=vilib_root, userlib_root=userlib_root
        )

    def clear(self) -> None:
        """Clear all loaded data."""
        self._graph.clear()
        self._vi_nodes.clear()
        self._term_to_node.clear()
        self._dep_graph.clear()
        self._stubs.clear()
        self._poly_info.clear()
        self._qualified_aliases.clear()
        self._name_to_keys.clear()
        self._qname_to_keys.clear()
        self._loaded_vis.clear()
        self._dep_load_mode.clear()
        self._source_paths.clear()
        self._search_paths = []
        self._vi_file_index = None
        self._vi_metadata.clear()
        self._vi_properties.clear()
        self._vi_health.clear()
        self._layouts.clear()
        self._parse_cache = None

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
            kind=LVTypeKind(parsed_type.kind),
            underlying_type=parsed_type.type_name,
            ref_type=parsed_type.ref_type,
            classname=parsed_type.classname,
            typedef_path=parsed_type.typedef_path,
            typedef_name=parsed_type.typedef_name,
            values=parsed_type.enum_values,
            element_type=self._enrich_type(parsed_type.element_type),
            dimensions=parsed_type.dimensions,
            measure_flavor=parsed_type.measure_flavor,
        )

        # Anonymous clusters: fields ARE the type definition (no external
        # identity to reference). Carry them on the terminal with full
        # recursive type info from the type_map.
        if parsed_type.fields and not parsed_type.classname:
            lv_type.fields = parsed_type.fields

        # Enrich enum values from vilib resolver (values are leaf data,
        # not structural — safe to carry on the terminal).
        if parsed_type.typedef_name:
            resolver = get_vilib_resolver()
            resolved = resolver.resolve_type(parsed_type.typedef_name)
            if resolved:
                if resolved.values:
                    lv_type.values = resolved.values
                lv_type.description = resolved.description

        # Class/typedef fields: codegen queries dep_graph or vilib_resolver
        # by classname/typedef_name. No copies.

        return lv_type

    def _dep_key_for_ref(
        self,
        ref: str,
        caller_vi_key: str | None = None,
    ) -> str | None:
        """Resolve a class/typedef reference to its dep-graph PATH key.

        ``ref`` may already BE a path key (returned as-is). Otherwise it is a
        NAME from a caller VI's ``type_map`` — a name is NOT unique (built vs
        source twins share a qname; two libraries share a ``Do.vi`` leaf), so it
        is resolved by CALLER-SCOPED graph query: among the caller VI's OWN dep
        successors (its actual edges, O(degree)), the node whose ``qname`` (or,
        failing that, bare ``name``) — identifying info stored ON the node —
        confirms this reference. A caller links exactly one path per dependency,
        so within the caller the match is unambiguous. Only that scoped match,
        an alias recorded from the caller's exact reference, or a qname that maps
        to exactly ONE node counts — NEVER a bare-name guess across the graph.
        Returns None rather than guess when nothing pins it to one node.
        """
        if self._dep_graph.has_node(ref):
            return ref
        if caller_vi_key is not None and self._dep_graph.has_node(caller_vi_key):
            succs = list(self._dep_graph.successors(caller_vi_key))
            nodes = self._dep_graph.nodes
            by_qname = [s for s in succs if nodes[s].get("qname") == ref]
            if len(by_qname) == 1:
                return by_qname[0]
            leaf = ref.rsplit(":", 1)[-1]
            by_name = [s for s in succs if self._dep_graph.nodes[s].get("name") == leaf]
            if len(by_name) == 1:
                return by_name[0]
        # No caller context: the alias recorded from a caller's exact reference,
        # then a qname that maps to exactly ONE node. No bare-name fallback.
        aliased = self._qualified_aliases.get(ref)
        if aliased is not None and self._dep_graph.has_node(aliased):
            return aliased
        keys = self._qname_to_keys.get(ref)
        if keys is None:
            target = ref.lower()
            for qname, qkeys in self._qname_to_keys.items():
                if qname.lower() == target:
                    keys = qkeys
                    break
        return keys[0] if keys and len(keys) == 1 else None

    def get_class_fields(
        self,
        classname: str,
        caller_vi_key: str | None = None,
    ) -> list[ClusterField] | None:
        """Complete field list for a class INCLUDING inherited parent fields.

        LabVIEW nMux field indices are into the combined (parent + own) field
        list, parent first — so the inheritance chain is walked. ``classname``
        is resolved to its PATH-key node via :meth:`_dep_key_for_ref` (pass the
        CALLER VI key so a duplicated class name resolves by the caller's edge);
        the parent chain is then followed by ``parent_key`` (the parent's PATH,
        recorded on the node) — never by name. Returns None if unresolved.
        """
        node_key = self._dep_key_for_ref(classname, caller_vi_key)
        if node_key is None or not self._dep_graph.has_node(node_key):
            return None
        return self._class_fields_by_key(node_key)

    def _parent_class_key(self, node_key: str) -> str | None:
        """PATH key of a class node's parent, or None. Prefer the ``parent_key``
        recorded at load time (the parent's PATH found by walk-up); fall back to
        resolving the recorded parent NAME among loaded classes — the parent may
        have been loaded SEPARATELY (a sibling dir), so walk-up never saw it. The
        class file names its parent by name only, so this is a legitimate
        name-boundary; it resolves to one node or None, never a guess."""
        data = self._dep_graph.nodes[node_key]
        pk: str | None = data.get("parent_key")
        if pk is not None and self._dep_graph.has_node(pk) and pk not in self._stubs:
            return pk
        parent_raw = data.get("parent_class")
        if parent_raw and parent_raw != "LabVIEW Object":
            cand = self._dep_key_for_ref(parent_raw + ".lvclass")
            if (
                cand is not None
                and cand not in self._stubs
                and self._dep_graph.nodes[cand].get("node_type") == "class"
            ):
                return cand
        return None

    def _class_fields_by_key(self, node_key: str) -> list[ClusterField] | None:
        """Own + inherited fields for the class/typedef node at PATH ``node_key``.
        Inheritance follows the parent's PATH key (:meth:`_parent_class_key`) by
        IDENTITY."""
        data = self._dep_graph.nodes[node_key]
        own_fields: list[ClusterField] = data.get("fields") or []
        parent_key = self._parent_class_key(node_key)
        if parent_key is not None:
            parent_fields = self._class_fields_by_key(parent_key)
            if parent_fields:
                return parent_fields + own_fields
        return own_fields

    def get_type_fields(
        self,
        lv_type: LVType,
        caller_vi_key: str | None = None,
    ) -> list[ClusterField] | None:
        """Get fields for any type. One API, all cases.

        Class types use dep_graph (authoritative, includes inheritance chain) —
        resolved by the CALLER's edge (``caller_vi_key``) so a duplicated class
        name maps to the right path. Typedef/cluster types prefer inline fields
        from the VI's own type_map (ground truth for that VI's dataflow, possibly
        newer than dep_graph); fall back to dep_graph when no inline fields.
        """
        if lv_type.classname:
            # OOP class: dep_graph with full inheritance chain is authoritative
            return self.get_class_fields(lv_type.classname, caller_vi_key)

        if lv_type.fields:
            # Inline fields from VI's own type_map take priority for non-class
            # types (e.g. typedef clusters). The VI's type_map is ground truth
            # for that specific VI's dataflow.
            return lv_type.fields

        if lv_type.typedef_name:
            # No inline fields: fall back to dep_graph (handles typedef_ref
            # types loaded from .ctl files, NI/DCAF types, etc.)
            return self.get_class_fields(lv_type.typedef_name, caller_vi_key)

        return None

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

    def get_graph_node(self, node_id: str) -> AnyGraphNode | None:
        """Get the typed graph node for a node_id."""
        if not self._graph.has_node(node_id):
            return None
        return self._graph.nodes[node_id].get("node")

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
        # NetworkX yields edges in construction-insertion order, which is
        # hash-randomized between processes. Sort by a stable key so callers
        # that take the first edge (get_source) or iterate (dependency tracing
        # in topological_sort_tiered) are byte-reproducible — otherwise the
        # discovered parallel tiers, and thus the generated code, vary by run.
        results.sort(key=lambda w: (w.node_id, w.terminal_id, w.index))
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
        results.sort(key=lambda w: (w.node_id, w.terminal_id, w.index))
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

    # === Context Manager ===

    def __enter__(self) -> InMemoryVIGraph:
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        self.clear()


def connect() -> InMemoryVIGraph:
    """Create an in-memory VI graph (no connection needed)."""
    return InMemoryVIGraph()

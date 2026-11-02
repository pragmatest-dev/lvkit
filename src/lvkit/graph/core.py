"""Core InMemoryVIGraph class definition and shared utilities.

Contains __init__, clear, _qid, _enrich_type, context manager, connect(),
set_var_name, get_var_name, incoming_edges, outgoing_edges, terminal_is_wired,
and module-level helper functions.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from ..library_folders import Folders, SearchEntries, folder_list, search_path_entries
from ..load_mode import LoadMode
from ..models import (
    ClusterField,
    LVType,
    LVTypeKind,
    WireStyle,
    contains_class_instance,
)
from ..parser.models import ParsedType, ParsedVI
from ..run_report import RunSettings
from ..type_names import ROOT_CLASS
from .search_path import SearchPath

if TYPE_CHECKING:
    from ..extract_pool import ExtractionPool
    from ..parser.layout import Layout
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .models import (
    AnyGraphNode,
    ConstantNode,
    DisableStructureNode,
    EventStructureNode,
    GlobalVariableNode,
    LabelNode,
    LocalVariableNode,
    PolyInfo,
    SharedVariableNode,
    StructureNode,
    VIDotNetAssemblies,
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
from .recorded_paths import RecordedPaths

# Map operation kind (the coarse op-kind string from _graph_node_to_op_kind)
# to its human-readable display label. Distinct key space from
# parser.node_types.get_display_name, which maps raw parser node_type tokens
# (e.g. "whileLoop", "forLoop") — do not try to merge the two.
_KIND_TO_TAGS: dict[str, str] = {
    "vi": "SubVI",
    "primitive": "Primitive",
    "caseStruct": "CaseStructure",
    "loop": "Loop",
    "operation": "Structure",
    "constant": "Constant",
    "formula": "FormulaNode",
    "local_variable": "LocalVariable",
    "global_variable": "GlobalVariable",
    "shared_variable": "SharedVariable",
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
    "global_variable",
    "shared_variable",
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


def uid_of(op_id: str) -> str:
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
    if isinstance(node, GlobalVariableNode):
        return "global_variable"
    if isinstance(node, SharedVariableNode):
        return "shared_variable"
    if isinstance(node, LabelNode):
        return "label"
    return "operation"


# Import mixins - these define methods that InMemoryVIGraph inherits
from .analysis import AnalysisMixin  # noqa: E402
from .construction import ConstructionMixin  # noqa: E402
from .load_problems import LoadProblemsMixin  # noqa: E402
from .loading import LoadingMixin  # noqa: E402
from .membership import MembershipMixin  # noqa: E402
from .operations import OperationsMixin  # noqa: E402
from .package_queries import PackageQueriesMixin  # noqa: E402
from .queries import QueryMixin  # noqa: E402
from .typedef import TypedefMixin  # noqa: E402
from .vi_reference import VIReferenceMixin  # noqa: E402
from .wire_queries import WireQueriesMixin  # noqa: E402


class InMemoryVIGraph(
    LoadProblemsMixin,
    LoadingMixin,
    MembershipMixin,
    ConstructionMixin,
    QueryMixin,
    OperationsMixin,
    PackageQueriesMixin,
    WireQueriesMixin,
    AnalysisMixin,
    TypedefMixin,
    VIReferenceMixin,
):
    """In-memory VI graph using a single unified NetworkX MultiDiGraph.

    Usage:
        graph = InMemoryVIGraph()
        graph.load_vi("/path/to/Main.vi", LoadMode.FULL)

        # Process VIs in dependency order (handles recursive VIs)
        for vi_group in graph.get_generation_order():
            for vi_name in vi_group:
                # Get nodes (topologically ordered)
                for node in graph.top_level_nodes(vi_name):
                    # ... generate code ...
    """

    def __init__(self, run_settings: RunSettings | None = None) -> None:
        # How a problem recorded on a VI's node is announced (echo, fail-fast).
        self.run_settings: RunSettings = run_settings or RunSettings()
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
        # The path each dependency's callers recorded for it, resolved.
        self._recorded = RecordedPaths(self._dep_graph)
        # Membership (graph/membership.py): member path -> the containers whose
        # file lists it, and container path -> the loaded VIs that claim it.
        self._listed_by: dict[str, set[str]] = {}
        self._claimants: dict[str, set[str]] = {}
        # Whether a load stops at a VI the catalog gives Python for (see
        # ``set_catalog_stop``).
        self._catalog_stop = False
        # Polymorphic VI info
        self._poly_info: dict[str, PolyInfo] = {}
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
        # lookup key.
        self._vi_display_names: dict[str, str] = {}
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
        # The .NET assemblies each VI calls -- a sibling facet to _vi_metadata.
        self._vi_dotnet_assemblies: dict[str, VIDotNetAssemblies] = {}
        # The folders <vilib> / <userlib> / <instrlib> path tokens name, in order,
        # and the VI Search Path for files not at their recorded path.
        self._vilib_roots: tuple[Path, ...] = ()
        self._userlib_roots: tuple[Path, ...] = ()
        self._instrlib_roots: tuple[Path, ...] = ()
        self._search_path = SearchPath(search_path_entries(None), {})
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
        # The process pool extracting this load's VIs ahead of the loader (see
        # extract_pool.py); set by the outermost load entry point, None outside.
        self._extract_pool: ExtractionPool | None = None

    def set_library_roots(
        self,
        vilib_root: Folders = None,
        userlib_root: Folders = None,
        instrlib_root: Folders = None,
        search_path: SearchEntries = None,
    ) -> None:
        """Set the folders <vilib>, <userlib> and <instrlib> path tokens name
        (each one or several, tried in order) and the VI Search Path
        (``search_path``, LabVIEW's default when None; ``graph/search_path.py``).

        Call before loading any VIs. When set, dependency refs with
        <vilib>, <userlib> or <instrlib> tokens resolve to real .vi files on
        disk instead of falling through to JSON-only lookup.

        Also feeds the same roots to the extraction cache so a VI's cache
        namespace is chosen by prefix-matching its resolved path against these
        real roots (shared vs. per-project), not by substring-scanning the path.
        This is the single wiring point for all entry points (CLI, MCP,
        pipeline, render, docs), which each call this before ``load_vi``.
        """
        self._vilib_roots = folder_list(vilib_root)
        self._userlib_roots = folder_list(userlib_root)
        self._instrlib_roots = folder_list(instrlib_root)
        pseudo = {
            "<vilib>": self._vilib_roots,
            "<userlib>": self._userlib_roots,
            "<instrlib>": self._instrlib_roots,
        }
        self._search_path = SearchPath(search_path_entries(search_path), pseudo)
        self.run_settings.library_roots = dict(pseudo)
        # The run's library roots feed the cache classifier; set them on the
        # stdlib-only cache_paths module (no pylabview pulled in, no cycle).
        from lvkit import cache_paths

        cache_paths.set_extraction_roots(
            vilib_root=self._vilib_roots,
            userlib_root=self._userlib_roots,
            instrlib_root=self._instrlib_roots,
        )

    def set_catalog_stop(self, stop: bool) -> None:
        """Make the next loads stop at a VI the vi.lib catalog gives Python for:
        only its own connector pane is loaded, never the VIs it calls. Code
        generation sets it, because those VIs are never generated; every other
        view loads the whole tree."""
        self._catalog_stop = stop

    def clear(self) -> None:
        """Clear all loaded data."""
        self._graph.clear()
        self._vi_nodes.clear()
        self._term_to_node.clear()
        self._dep_graph.clear()
        self._stubs.clear()
        self._recorded.clear()
        self._poly_info.clear()
        self._loaded_vis.clear()
        self._dep_load_mode.clear()
        self._source_paths.clear()
        self._vi_metadata.clear()
        self._vi_properties.clear()
        self._vi_health.clear()
        self._vi_dotnet_assemblies.clear()
        self._layouts.clear()
        self._parse_cache = None

    @staticmethod
    def _qid(vi_path: str, uid: str) -> str:
        """Qualify a parser UID with the VI PATH (its identity) to prevent
        cross-VI collisions -- the node key is ``<vi_path>::<uid>``."""
        return f"{vi_path}::{uid}"

    def _enrich_type(
        self, parsed_type: ParsedType | None, file_key: str
    ) -> LVType | None:
        """Enrich ParsedType from parser to LVType with vilib data.

        Parser outputs ParsedType with basic info from single VI's XML.
        This enriches it with typedef details (enum values, cluster fields)
        from vilib_resolver, and a class with its wire style: the class the
        name denotes in ``file_key``, the VI or control the type is in.
        """
        if parsed_type is None:
            return None

        lv_type = LVType(
            kind=LVTypeKind(parsed_type.kind),
            underlying_type=parsed_type.type_name,
            ref_type=parsed_type.ref_type,
            server_class=parsed_type.server_class,
            classname=parsed_type.classname,
            class_file=parsed_type.class_file,
            typedef_path=parsed_type.typedef_path,
            typedef_name=parsed_type.typedef_name,
            typedef_file=parsed_type.typedef_file,
            values=parsed_type.enum_values,
            element_type=self._enrich_type(parsed_type.element_type, file_key),
            dimensions=parsed_type.dimensions,
            measure_flavor=parsed_type.measure_flavor,
            user_event_name=parsed_type.user_event_name,
            dotnet_type=parsed_type.dotnet_type,
            unreadable=parsed_type.unreadable,
            pane=parsed_type.pane,
            type_id=parsed_type.type_id,
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

        # A class instance carries its OWN wire style (color/width/line style),
        # resolved from its .lvclass, so the one render lookup draws every class
        # wire in the class's style with no graph — see LVType.wire_style /
        # render.style.wire_style.
        if parsed_type.classname or parsed_type.typedef_name:
            lv_type.file_key = self.recorded_type_key(file_key, lv_type)
        if parsed_type.classname:
            class_key = self.class_type_key(lv_type)
            if class_key is not None:
                lv_type.wire_style = self.class_wire_style(class_key)

        return lv_type

    def with_class_levels(self, lv_type: LVType | None) -> LVType | None:
        """``lv_type`` with every class instance in it (itself, or in a field,
        element, key or value) carrying its class's private-data fields, so a
        stored value of the type reads its private data; the fields of those
        levels get the same treatment, so a class held in another class's private
        data reads its own. Each class is the file its type's ``file_key`` names.
        A class already being stamped further up (its private data holds itself,
        directly or through another class) is left without levels: that inner
        instance shows its name and keeps its exact extent. Types without a
        loaded class come back unchanged; a changed one is a copy, never an edit
        of a type shared with
        the parser."""
        return self._stamp_class_levels(lv_type, ())

    def _stamp_class_levels(
        self,
        lv_type: LVType | None,
        enclosing: tuple[str, ...],
    ) -> LVType | None:
        if lv_type is None:
            return None
        replacements: dict[str, object] = {}
        if lv_type.underlying_type == "Refnum" and lv_type.classname:
            class_key = lv_type.file_key
            levels = (
                self.class_private_levels(class_key) if class_key is not None else None
            )
            if (
                class_key is not None
                and class_key not in enclosing
                and levels is not None
            ):
                inner = (*enclosing, class_key)
                replacements["class_levels"] = [
                    [self._stamp_field(f, inner) for f in level] for level in levels
                ]
        if lv_type.element_type is not None:
            element = self._stamp_class_levels(lv_type.element_type, enclosing)
            if element is not lv_type.element_type:
                replacements["element_type"] = element
        if lv_type.value_type is not None:
            value = self._stamp_class_levels(lv_type.value_type, enclosing)
            if value is not lv_type.value_type:
                replacements["value_type"] = value
        if lv_type.fields and contains_class_instance(lv_type):
            replacements["fields"] = [
                self._stamp_field(f, enclosing) for f in lv_type.fields
            ]
        return dataclasses.replace(lv_type, **replacements) if replacements else lv_type

    def _stamp_field(
        self, field: ClusterField, enclosing: tuple[str, ...]
    ) -> ClusterField:
        if field.type is None:
            return field
        stamped = self._stamp_class_levels(field.type, enclosing)
        return (
            field if stamped is field.type else dataclasses.replace(field, type=stamped)
        )

    def class_private_levels(self, class_key: str) -> list[list[ClusterField]] | None:
        """The private-data fields of each level of the hierarchy of the class at
        ``class_key``, oldest ancestor first -- the clusters a flattened instance
        holds, one per level. None when the class, or an ancestor other than
        LabVIEW Object, is not loaded (its bytes are then skipped by their stored
        count)."""
        node_key: str | None = class_key
        levels: list[list[ClusterField]] = []
        while node_key is not None:
            if not self._dep_graph.has_node(node_key) or node_key in self._stubs:
                return None
            data = self._dep_graph.nodes[node_key]
            levels.append(list(data.get("fields") or []))
            parent_raw = data.get("parent_class")
            node_key = self._parent_class_key(node_key)
            if node_key is None and parent_raw and parent_raw != ROOT_CLASS:
                return None
        return levels[::-1] or None

    def class_type_key(self, lv_type: LVType) -> str | None:
        """PATH key of the loaded class node a class-typed ``lv_type`` is
        (``LVType.file_key``); ``None`` for a type that is not a loaded class."""
        key = lv_type.file_key
        if not lv_type.classname or key is None or not self._dep_graph.has_node(key):
            return None
        return key if self.is_class(key) else None

    def _parent_class_key(self, node_key: str) -> str | None:
        """PATH key of a class node's loaded parent, or None: the ``parent_key``
        recorded at load time, the path the class file records for its parent."""
        pk: str | None = self._dep_graph.nodes[node_key].get("parent_key")
        if pk is not None and self._dep_graph.has_node(pk) and pk not in self._stubs:
            return pk
        return None

    def get_class_fields(self, class_key: str) -> list[ClusterField] | None:
        """The private-data fields the class at ``class_key`` declares itself, in
        declaration order, not those it inherits (a Bundle/Unbundle By Name on a
        class wire counts the wire's own class's fields only: private data is
        private to its class). None when the graph has no such node. The fields
        of every level of the hierarchy are :meth:`class_private_levels`."""
        if not self._dep_graph.has_node(class_key):
            return None
        return self._class_fields_by_key(class_key)

    def _class_fields_by_key(self, node_key: str) -> list[ClusterField] | None:
        """The fields the class/typedef node at PATH ``node_key`` declares."""
        return list(self._dep_graph.nodes[node_key].get("fields") or [])

    def class_fields_with_inherited(self, node_key: str) -> list[ClusterField]:
        """The fields of the class at PATH ``node_key`` and of every ancestor,
        the oldest ancestor's first: what a class and its parents hold together,
        for a view that lists them (a Bundle/Unbundle By Name counts only the
        wire's own class, :meth:`get_type_fields`)."""
        parent_key = self._parent_class_key(node_key)
        inherited = (
            self.class_fields_with_inherited(parent_key)
            if parent_key is not None
            else []
        )
        return [*inherited, *(self._class_fields_by_key(node_key) or [])]

    def class_wire_style(self, node_key: str) -> WireStyle | None:
        """This class's own wire style, else the nearest ancestor's (LabVIEW's
        "use parent's/default design"), following the parent's PATH key
        (:meth:`_parent_class_key`); None when it and every ancestor use the
        default look."""
        style: WireStyle | None = self._dep_graph.nodes[node_key].get("wire_style")
        if style is not None:
            return style
        parent_key = self._parent_class_key(node_key)
        if parent_key is not None:
            return self.class_wire_style(parent_key)
        return None

    def get_type_fields(self, lv_type: LVType) -> list[ClusterField] | None:
        """Get fields for any type. One API, all cases.

        A class type's fields are the ones its class declares (a Bundle/Unbundle
        By Name on a class wire counts the wire's own class's fields only:
        private data is private to its class), read from the class file
        ``LVType.file_key`` names. Typedef/cluster
        types prefer inline fields from the VI's own type_map (ground truth for
        that VI's dataflow, possibly newer than the control); else the loaded
        control's.
        """
        if lv_type.classname:
            return self._named_fields(lv_type)

        if lv_type.fields:
            # Inline fields from VI's own type_map take priority for non-class
            # types (e.g. typedef clusters). The VI's type_map is ground truth
            # for that specific VI's dataflow.
            return lv_type.fields

        if lv_type.typedef_name:
            # No inline fields: the loaded control's (typedef_ref types loaded
            # from .ctl files, NI/DCAF types, etc.)
            return self._named_fields(lv_type)

        return None

    def _named_fields(self, lv_type: LVType) -> list[ClusterField] | None:
        """The fields of the class or control file ``lv_type.file_key`` names;
        None when it names no node."""
        key = lv_type.file_key
        if key is None or not self._dep_graph.has_node(key):
            return None
        return self._class_fields_by_key(key)

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

    def terminal_vi(self, terminal_id: str) -> str | None:
        """The VI (its key) whose diagram or interface the terminal belongs to."""
        node_id = self._term_to_node.get(terminal_id)
        if not node_id or not self._graph.has_node(node_id):
            return None
        gnode = self._graph.nodes[node_id].get("node")
        return gnode.vi_path if gnode else None

    def clear_var_names(self, vi_name: str) -> None:
        """Clear the Python ``var_name`` scratch state on every terminal of a
        VI's nodes.

        ``var_name`` is generation-time scratch that codegen writes onto the
        graph's terminals. Clearing it before a build makes code generation
        IDEMPOTENT: a second ``build_module`` over the same graph starts from the
        same clean state as the first and produces identical output, instead of
        resolving through a prior run's stale bindings. A no-op on a freshly
        loaded graph (the normal single-generation path)."""
        for node_id in self._vi_nodes.get(vi_name, set()):
            gnode = self._graph.nodes.get(node_id, {}).get("node")
            if gnode is None:
                continue
            for t in gnode.terminals:
                t.var_name = None

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

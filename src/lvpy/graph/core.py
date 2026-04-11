"""Core InMemoryVIGraph class definition and shared utilities.

Contains __init__, clear, _qid, _enrich_type, context manager, connect(),
set_var_name, get_var_name, incoming_edges, outgoing_edges, terminal_is_wired,
_kind_to_labels, and module-level helper functions.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from ..models import ClusterField, LVType
from ..parser.models import ParsedType
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .models import (
    AnyGraphNode,
    ConstantNode,
    PolyInfo,
    StructureNode,
    VIMetadata,
    VINode,
    WireEnd,
)
from .models import (
    PrimitiveNode as GraphPrimitiveNode,
)

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
        # Polymorphic VI info
        self._poly_info: dict[str, PolyInfo] = {}
        # Qualified name aliases: "Lib.lvlib:VI.vi" -> "VI.vi" (for library VIs)
        self._qualified_aliases: dict[str, str] = {}
        # Track loaded VIs across multiple load_vi() calls to prevent re-parsing
        self._loaded_vis: set[str] = set()
        # Source file paths: vi_name -> Path to original .vi file
        self._source_paths: dict[str, Path] = {}
        # VI metadata
        self._vi_metadata: dict[str, VIMetadata] = {}

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
            values=parsed_type.enum_values,
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

    def get_class_fields(
        self, classname: str,
    ) -> list[ClusterField] | None:
        """Get fields for a named type from dep_graph by key."""
        if not self._dep_graph.has_node(classname):
            return None
        fields: list[ClusterField] | None = (
            self._dep_graph.nodes[classname].get("fields")
        )
        return fields

    def get_type_fields(
        self, lv_type: LVType,
    ) -> list[ClusterField] | None:
        """Get fields for any type. One API, all cases.

        Named types (class, typedef) → dep_graph lookup.
        Anonymous clusters → inline fields on the type itself.
        """
        name = lv_type.classname or lv_type.typedef_name
        if name:
            return self.get_class_fields(name)
        return lv_type.fields

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
        return []

    # === Context Manager ===

    def __enter__(self) -> InMemoryVIGraph:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.clear()


def connect() -> InMemoryVIGraph:
    """Create an in-memory VI graph (no connection needed)."""
    return InMemoryVIGraph()

"""Types for VI graph representation.

Two layers:
- Graph node types (Pydantic): VINode, PrimitiveNode, StructureNode, ConstantNode
  Stored on the unified nx.MultiDiGraph as node["node"] = SomeNode(...)
- Codegen types (dataclasses): Operation, Terminal, Constant
  Consumed by the code generation pipeline (converted from graph nodes)

Uses Pydantic BaseModel for graph types. Existing dataclasses kept for
codegen compatibility until full migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

# Type alias for scalar constant/default values
ScalarValue = str | int | float | bool | None

# ============================================================
# Shared types (used by both graph and codegen layers)
# ============================================================


@dataclass
class LVType:
    """LabVIEW type structure - unified representation for all types."""

    kind: str  # "primitive", "enum", "cluster", "array", "ring", "typedef_ref"
    underlying_type: str | None = None
    ref_type: str | None = None
    classname: str | None = None

    values: dict[str, EnumValue] | None = None
    fields: list[ClusterField] | None = None
    element_type: LVType | None = None
    dimensions: int | None = None
    typedef_path: str | None = None
    typedef_name: str | None = None
    description: str | None = None

    def to_python(self) -> str:
        """Render as Python type annotation string."""
        if self.kind == "primitive":
            # Refnum with class name → use the class type
            if self.underlying_type == "Refnum" and self.classname:
                name = _sanitize_type_name(self.classname.replace(".lvclass", ""))
                return name or "Any"
            return _LV_TO_PYTHON_TYPE.get(self.underlying_type or "", "Any")
        elif self.kind == "array":
            inner = self.element_type.to_python() if self.element_type else "Any"
            result = f"list[{inner}]"
            for _ in range((self.dimensions or 1) - 1):
                result = f"list[{result}]"
            return result
        elif self.kind == "cluster":
            if self.typedef_name:
                name = _sanitize_type_name(self.typedef_name)
                return name or "dict[str, Any]"
            return "dict[str, Any]"
        elif self.kind in ("enum", "ring"):
            if self.typedef_name:
                name = _sanitize_type_name(self.typedef_name)
                return name or "int"
            return "int"
        elif self.kind == "typedef_ref":
            if self.typedef_name:
                name = _sanitize_type_name(self.typedef_name)
                return name or "Any"
            return "Any"
        return "Any"


@dataclass
class EnumValue:
    """A single value in an enum typedef."""

    value: int
    description: str | None = None


@dataclass
class ClusterField:
    """A field in a cluster."""

    name: str
    type: LVType | None = None


def _is_error_cluster(lv_type: LVType) -> bool:
    """Check if a type is an error cluster.

    Detects error clusters by:
    1. TypeDef name contains "error" (case-insensitive)
    2. Cluster with status/code/source fields
    """
    if lv_type.kind not in ("cluster", "typedef_ref"):
        return False

    # Check typedef name
    typedef_name = lv_type.typedef_name or ""
    if "error" in typedef_name.lower():
        return True

    # Check field names for error cluster pattern
    if lv_type.fields:
        field_names = {f.name.lower() for f in lv_type.fields}
        error_fields = {"status", "code", "source"}
        if error_fields <= field_names:
            return True

    return False


class TypeResolutionNeeded(Exception):
    """Raised when a named type dependency cannot be resolved.

    Same pattern as VILibResolutionNeeded / TerminalResolutionNeeded.
    The type is referenced but not loaded in the dep_graph.
    """

    def __init__(self, type_name: str, context: str = ""):
        self.type_name = type_name
        self.context = context
        msg = f"Type resolution needed for '{type_name}'"
        if context:
            msg += f" (referenced by {context})"
        super().__init__(msg)


class Terminal(BaseModel):
    """A connection point on a node. Edges connect to them."""

    model_config = {"arbitrary_types_allowed": True}

    id: str
    index: int
    direction: str  # "input" or "output"
    name: str | None = None
    lv_type: LVType | None = None
    var_name: str | None = None  # set during codegen
    nmux_role: str | None = None  # "agg" or "list"
    nmux_field_index: int | None = None  # class field index
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    default_value: ScalarValue = None

    def python_type(self) -> str:
        """Python type string derived from lv_type."""
        return self.lv_type.to_python() if self.lv_type else "Any"

    @property
    def is_error_cluster(self) -> bool:
        """Check if this terminal carries a LabVIEW error cluster."""
        if self.lv_type and _is_error_cluster(self.lv_type):
            return True
        name = (self.name or "").lower()
        return "error" in name and (
            "in" in name or "out" in name or "no error" in name
        )


class FPTerminal(Terminal):
    """A connector pane terminal on a VINode."""

    kind: Literal["fp"] = "fp"
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    is_indicator: bool = False
    is_public: bool = True
    control_type: str | None = None
    default_value: ScalarValue = None
    enum_values: list[str] = []


class TunnelTerminal(Terminal):
    """An outer or inner tunnel terminal on a StructureNode."""

    kind: Literal["tunnel"] = "tunnel"
    tunnel_type: str = ""  # "lSR", "rSR", "lpTun", "lMax", "caseSel"
    boundary: str = ""  # "outer" or "inner"
    paired_id: str | None = None  # matching terminal on other side


@dataclass
class Tunnel:
    """A tunnel connecting structure outer/inner terminals."""

    outer_terminal_uid: str
    inner_terminal_uid: str
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax", "caseSel", etc.
    paired_terminal_uid: str | None = None

    @property
    def direction(self) -> str:
        if self.tunnel_type == "lSR":
            return "in"
        if self.tunnel_type in ("rSR", "lMax"):
            return "out"
        return "unknown"


class PropertyDef(BaseModel):
    """A property read/write on a property node."""

    name: str


# ============================================================
# Graph node types (Pydantic) — stored on nx.MultiDiGraph
# ============================================================


class GraphNode(BaseModel):
    """Base for all graph nodes.

    Every node has terminals — that's what edges connect to.
    Subclasses add kind-specific fields via discriminated union.

    Containment: nodes inside structures have `parent` set to the
    structure's UID and `frame` set to the frame selector value.
    Top-level nodes have parent=None.
    """

    model_config = {"arbitrary_types_allowed": True}

    id: str
    vi: str
    name: str | None = None
    node_type: str | None = None
    terminals: list[Terminal] = []
    description: str | None = None
    parent: str | None = None  # containing structure UID
    frame: str | int | None = None  # frame selector value


class VINode(GraphNode):
    """A VI. Terminals = FP controls/indicators (connector pane).

    Used for both VI definitions (top-level) and SubVI calls
    (placed on another VI's diagram). The graph structure tells
    you which — SubVI calls have a parent VI, top-level VIs don't.
    """

    kind: Literal["vi"] = "vi"
    library: str | None = None
    qualified_name: str | None = None
    poly_variant_name: str | None = None


class PrimitiveNode(GraphNode):
    """A LabVIEW primitive (Add, Index Array, String Length, etc.)."""

    kind: Literal["primitive"] = "primitive"
    prim_id: int | None = None
    prim_index: int | None = None
    operation: str | None = None  # cpdArith: "or", "and", "add"
    object_name: str | None = None  # property/invoke
    object_method_id: str | None = None
    properties: list[PropertyDef] = []
    method_name: str | None = None
    method_code: int | None = None


class FrameInfo(BaseModel):
    """Metadata about a frame in a case structure or flat sequence.

    The actual operations in each frame are graph nodes with
    parent=structure_uid and frame=selector_value.
    """

    selector_value: str | int
    is_default: bool = False


class StructureNode(GraphNode):
    """A loop, case structure, or flat sequence.

    Terminals = tunnel outer/inner terminals. Each tunnel creates two
    Terminal objects (outer + inner) connected by an internal edge.
    Tunnel metadata (tunnel_type, boundary, paired_id) is on each Terminal.

    Inner operations are NOT listed here — they're graph nodes with
    parent=this structure's UID. For case/sequence structures, each
    inner operation also has frame=selector_value.
    """

    kind: Literal["structure"] = "structure"
    loop_type: str | None = None
    stop_condition_terminal: str | None = None
    frames: list[FrameInfo] = []  # frame metadata (empty for loops)
    selector_terminal: str | None = None


class ConstantNode(GraphNode):
    """A constant value. One output terminal (index 0)."""

    kind: Literal["constant"] = "constant"
    value: ScalarValue = None
    lv_type: LVType | None = None
    raw_value: str | None = None
    label: str | None = None


# Discriminated union of all node types
AnyGraphNode = VINode | PrimitiveNode | StructureNode | ConstantNode


# ============================================================
# Wire types (Pydantic) — stored on graph edges
# ============================================================


class WireEnd(BaseModel):
    """One end of a wire — identifies the terminal and its parent node."""

    model_config = {"frozen": True}

    terminal_id: str
    node_id: str
    index: int | None = None
    name: str | None = None
    labels: list[str] = []


class Wire(BaseModel):
    """A wire (edge) in the dataflow graph.

    Each wire connects a source WireEnd to a destination WireEnd.
    """

    model_config = {"frozen": True}

    source: WireEnd
    dest: WireEnd

    @classmethod
    def from_terminals(
        cls,
        from_terminal_id: str,
        to_terminal_id: str,
        from_parent_id: str | None = None,
        to_parent_id: str | None = None,
        from_parent_name: str | None = None,
        to_parent_name: str | None = None,
        from_parent_labels: list[str] | None = None,
        to_parent_labels: list[str] | None = None,
        from_slot_index: int | None = None,
        to_slot_index: int | None = None,
    ) -> Wire:
        """Create Wire from flat terminal args (backward compat for tests)."""
        return cls(
            source=WireEnd(
                terminal_id=from_terminal_id,
                node_id=from_parent_id or from_terminal_id,
                index=from_slot_index,
                name=from_parent_name,
                labels=from_parent_labels or [],
            ),
            dest=WireEnd(
                terminal_id=to_terminal_id,
                node_id=to_parent_id or to_terminal_id,
                index=to_slot_index,
                name=to_parent_name,
                labels=to_parent_labels or [],
            ),
        )

    # Backward-compatible properties for codegen consumers
    @property
    def from_terminal_id(self) -> str:
        return self.source.terminal_id

    @property
    def to_terminal_id(self) -> str:
        return self.dest.terminal_id

    @property
    def from_parent_id(self) -> str:
        return self.source.node_id

    @property
    def to_parent_id(self) -> str:
        return self.dest.node_id

    @property
    def from_parent_name(self) -> str | None:
        return self.source.name

    @property
    def to_parent_name(self) -> str | None:
        return self.dest.name

    @property
    def from_parent_labels(self) -> list[str]:
        return self.source.labels

    @property
    def to_parent_labels(self) -> list[str]:
        return self.dest.labels

    @property
    def from_slot_index(self) -> int | None:
        return self.source.index

    @property
    def to_slot_index(self) -> int | None:
        return self.dest.index


# ============================================================
# Codegen types (dataclasses) — consumed by code generation
# Converted from graph nodes by memory_graph.get_operations()
# ============================================================


@dataclass
class CaseFrame:
    """A frame in a case structure or sequence — codegen only.

    Built by _build_operation() from graph nodes with matching
    parent and frame attributes.
    """

    selector_value: str | int
    inner_node_uids: list[str] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    is_default: bool = False


@dataclass
class Operation:
    """An operation node for code generation.

    Built from GraphNode subclasses by InMemoryVIGraph._build_operation().
    """

    id: str
    name: str | None
    labels: list[str]
    primResID: int | None = None
    terminals: list[Terminal] = field(default_factory=list)
    node_type: str | None = None
    loop_type: str | None = None
    tunnels: list[Tunnel] = field(default_factory=list)
    inner_nodes: list[Operation] = field(default_factory=list)
    stop_condition_terminal: str | None = None
    description: str | None = None
    operation: str | None = None
    object_name: str | None = None
    object_method_id: str | None = None
    properties: list[PropertyDef] = field(default_factory=list)
    method_name: str | None = None
    method_code: int | None = None
    case_frames: list[CaseFrame] = field(default_factory=list)
    selector_terminal: str | None = None
    poly_variant_name: str | None = None


@dataclass
class Constant:
    """A constant value for code generation."""

    id: str
    value: ScalarValue
    lv_type: LVType | None = None
    raw_value: str | None = None
    name: str | None = None


@dataclass
class VIContext:
    """Complete VI context for code generation."""

    name: str
    library: str | None = None
    qualified_name: str | None = None
    inputs: list[Terminal] = field(default_factory=list)
    outputs: list[Terminal] = field(default_factory=list)
    constants: list[Constant] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    has_parallel_branches: bool = False
    # Legacy fields (LLM agent pipeline)
    terminals: list[dict[str, Any]] = field(default_factory=list)
    data_flow: list[Wire] = field(default_factory=list)
    subvi_calls: list[dict[str, Any]] = field(default_factory=list)
    poly_variants: list[str] = field(default_factory=list)


@dataclass
class PolyInfo:
    """Polymorphic VI metadata."""

    is_polymorphic: bool = True
    variants: list[str] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)


@dataclass
class VIMetadata:
    """VI metadata from main XML."""

    library: str | None = None
    qualified_name: str | None = None


# ============================================================
# Source/Destination info types (returned by context queries)
# ============================================================


@dataclass
class SourceInfo:
    """Source terminal info from an incoming edge."""

    src_terminal: str
    src_parent_id: str
    src_parent_name: str | None = None
    src_parent_labels: list[str] = field(default_factory=list)
    src_slot_index: int | None = None


@dataclass
class DestinationInfo:
    """Destination terminal info from an outgoing edge."""

    dest_terminal: str
    dest_parent_id: str
    dest_parent_name: str | None = None
    dest_parent_labels: list[str] = field(default_factory=list)
    dest_slot_index: int | None = None


# ============================================================
# Query result types (returned by graph queries)
# ============================================================


@dataclass
class ConstantInfo:
    """A constant value discovered across VIs."""

    vi_name: str
    value: str
    label: str | None
    type: str
    python: ScalarValue


@dataclass
class PrimitiveInfo:
    """A primitive node discovered across VIs."""

    vi_name: str
    prim_id: int | None
    input_types: list[str]
    output_types: list[str]


@dataclass
class ClusterInfo:
    """A cluster type discovered across VIs."""

    name: str
    id: str
    vis: list[str]


@dataclass
class StubTerminalInfo:
    """Terminal info for a stub VI."""

    name: str
    type: str


@dataclass
class StubVIInfo:
    """Info about a stub VI (missing dependency)."""

    name: str
    vilib_path: str | None = None
    python_hint: str | None = None
    inputs: list[StubTerminalInfo] = field(default_factory=list)
    outputs: list[StubTerminalInfo] = field(default_factory=list)
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)


# ============================================================
# Error handling types
# ============================================================


@dataclass
class BranchPoint:
    """A point where one output feeds multiple inputs (fork)."""

    source_terminal: str
    source_operation: str | None
    destinations: list[str]
    vi_name: str | None = None


@dataclass
class ParallelBranch:
    """A single branch from a branch point to a merge point."""

    branch_id: int
    source_terminal: str
    operation_ids: list[str]
    merge_terminal: str | None
    merge_operation: str | None


# ============================================================
# Utilities
# ============================================================

_LV_TO_PYTHON_TYPE: dict[str, str] = {
    "NumInt8": "int",
    "NumInt16": "int",
    "NumInt32": "int",
    "NumInt64": "int",
    "NumUInt8": "int",
    "NumUInt16": "int",
    "NumUInt32": "int",
    "NumUInt64": "int",
    "NumFloat32": "float",
    "NumFloat64": "float",
    "String": "str",
    "Boolean": "bool",
    "Path": "Path",
    "Variant": "Any",
    "LVVariant": "Any",
    "Refnum": "Any",
    "Void": "None",
}


def control_type_to_lvtype(control_type: str) -> LVType | None:
    """Map a LabVIEW control type to LVType."""
    mapping = {
        "stdPath": LVType(kind="primitive", underlying_type="Path"),
        "stdString": LVType(kind="primitive", underlying_type="String"),
        "stdBool": LVType(kind="primitive", underlying_type="Boolean"),
        "stdNum": LVType(kind="primitive", underlying_type="NumFloat64"),
        "stdDBL": LVType(kind="primitive", underlying_type="NumFloat64"),
        "stdI32": LVType(kind="primitive", underlying_type="NumInt32"),
        "stdI16": LVType(kind="primitive", underlying_type="NumInt16"),
        "stdU32": LVType(kind="primitive", underlying_type="NumUInt32"),
        "stdU16": LVType(kind="primitive", underlying_type="NumUInt16"),
    }
    return mapping.get(control_type)


def _sanitize_type_name(typedef_name: str) -> str:
    """Sanitize a typedef name into a valid Python identifier."""
    name = typedef_name.split(":")[-1].replace(".ctl", "")
    name = "".join(c for c in name if c.isalnum() or c == "_")
    return name

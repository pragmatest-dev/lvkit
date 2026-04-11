"""Graph node and codegen context models.

These types are produced by the graph layer and consumed by codegen.
Parser never imports from this module.

Dependency: lvpy.models (shared primitives + flow types)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..models import (
    CaseFrame,
    LVType,
    Operation,
    PropertyDef,
    ScalarValue,
    SequenceFrame,
    Terminal,
)

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
    # Fully qualified on-disk path components joined with /, e.g.
    # "<vilib>/Utility/error.llb/Error Cluster From Error Code.vi".
    # Set on SubVI call nodes when the parser captured a path ref.
    qualified_path: str | None = None


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


class StructureNode(GraphNode):
    """Base for structure nodes (case, loop, sequence).

    Terminals = tunnel outer/inner terminals. Each tunnel creates two
    Terminal objects (outer + inner) connected by an internal edge.

    Inner operations are separate graph nodes with parent=this UID.
    """

    kind: Literal["structure"] = "structure"


class CaseStructureNode(StructureNode):
    """A case/select structure with selector-driven frames."""

    model_config = {"arbitrary_types_allowed": True}

    selector_terminal: str | None = None
    frames: list[CaseFrame] = []


class LoopNode(StructureNode):
    """A while or for loop."""

    loop_type: str | None = None  # "whileLoop" or "forLoop"
    stop_condition_terminal: str | None = None


class SequenceNode(StructureNode):
    """A flat or stacked sequence with ordered frames."""

    model_config = {"arbitrary_types_allowed": True}

    frames: list[SequenceFrame] = []


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
# Codegen context types (Pydantic)
# ============================================================


class Constant(BaseModel):
    """A constant value for code generation."""

    model_config = {"arbitrary_types_allowed": True}

    id: str
    value: ScalarValue
    lv_type: LVType | None = None
    raw_value: str | None = None
    name: str | None = None


class SubVICall(BaseModel):
    """A SubVI call reference in VIContext."""

    call_name: str | None = None
    vi_name: str | None = None


class TerminalRef(BaseModel):
    """A terminal reference in VIContext (legacy skeleton generator support)."""

    id: str
    parent_id: str
    index: int
    type: str
    name: str | None = None
    direction: str


class VIContext(BaseModel):
    """Complete VI context for code generation."""

    model_config = {"arbitrary_types_allowed": True}

    name: str
    library: str | None = None
    qualified_name: str | None = None
    inputs: list[Terminal] = []
    outputs: list[Terminal] = []
    constants: list[Constant] = []
    operations: list[Operation] = []
    has_parallel_branches: bool = False
    terminals: list[TerminalRef] = []
    data_flow: list[Wire] = []
    subvi_calls: list[SubVICall] = []
    poly_variants: list[str] = []


# ============================================================
# VI metadata types (dataclasses)
# ============================================================


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

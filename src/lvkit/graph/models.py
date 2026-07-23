"""Graph node and codegen context models.

These types are produced by the graph layer and consumed by codegen.
Parser never imports from this module.

Dependency: lvkit.models (shared primitives + flow types)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from ..models import (
    CaseFrame,
    ClusterField,
    EventFrame,
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
    # Invoke node only: qualified terminal ids from the parser's dcoList, in
    # row order (2 per row -- row 0 = method [select-slot, return-value],
    # rows 1..N = params [input, output]). Ids match ``terminals[i].id``.
    # See render/nodes.py:_invoke_node_glyph for how rows are built from this.
    invoke_row_terminal_ids: list[str] = []


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
    # Frame LabVIEW last displayed (the faithful initial view), recovered from
    # the dataspace selector table. None when not correlated → renderer falls
    # back to the default frame, then frame 0.
    displayed_frame: int | None = None
    # "Case Insensitive Match" enabled (string selectors) → renderer draws the
    # "A=a" badge. Default string matching is case-sensitive.
    case_insensitive: bool = False


class LoopNode(StructureNode):
    """A while or for loop."""

    loop_type: str | None = None  # "whileLoop" or "forLoop"
    stop_condition_terminal: str | None = None
    # While loop conditional-terminal polarity. True = Continue-if-True,
    # False = Stop-if-True (default). See ParsedLoopStructure for the
    # data evidence backing this polarity mapping.
    stop_condition_inverted: bool = False


class SequenceNode(StructureNode):
    """A flat or stacked sequence with ordered frames."""

    model_config = {"arbitrary_types_allowed": True}

    frames: list[SequenceFrame] = []


class InPlaceNode(StructureNode):
    """An In Place Element Structure (decompose/recompose).

    In Python, objects are mutable references so IPES is transparent —
    no control flow, just field access and in-place write-back.
    """

    pass


class DisableStructureNode(StructureNode):
    """A Diagram/Conditional Disable structure.

    Frame-bearing like a case (one of several subdiagrams is active), but
    has NO selector terminal -- the active frame is fixed at compile/edit
    time (the heap's ``activeDiag``, driven by a conditional-compile symbol
    or the user's Enable/Disable toggle), not chosen by a runtime wire
    value. Kept as its own type (NOT a CaseStructureNode subclass) so it
    does not fall into case-structure codegen, which assumes a real runtime
    selector -- render treats it like a case (see render/scene.py,
    render/draw.py); codegen has no dedicated generator for it yet (falls
    through to the existing "unknown node type" comment fallback).
    """

    model_config = {"arbitrary_types_allowed": True}

    frames: list[CaseFrame] = []
    # Index into ``frames`` of the enabled/active subdiagram at compile/edit
    # time (heap ``activeDiag``) -- the faithful initial view. None if
    # unresolved.
    active_frame: int | None = None


class EventStructureNode(StructureNode):
    """An Event Structure — one frame per registered event.

    Unlike CaseStructureNode, the active frame is chosen at RUNTIME by
    whichever event actually fires — there is no selector wire/value, so
    frames are keyed by INDEX (matching a stacked sequence's convention, not
    a case's ``selector_value``). See EventFrame.
    """

    model_config = {"arbitrary_types_allowed": True}

    frames: list[EventFrame] = []
    # Frame LabVIEW last displayed (heap ``dIdx``), whose ``event_label``
    # came from the heap's own precomputed ``selString`` text — the
    # faithful initial view. None if unresolved (renderer falls back to
    # frame 0).
    displayed_frame: int | None = None
    # Qualified (vi-prefixed) node ids of this structure's Event FILTER Nodes
    # — same heap class (``eventDataNode``) as an Event DATA Node, so this is
    # the only way to tell which is which (see parser/nodes/event.py). Used
    # by the renderer to draw the Filter Node's accent band on the opposite
    # (right) edge from a Data Node's (left) — see render/nodes.py.
    filter_node_uids: frozenset[str] = frozenset()


class FormulaNode(GraphNode):
    """A Formula Node (fBox) — an embedded C-like script over typed terminals.

    Terminals carry the script's variables (name, type, direction). The
    script is compiled to native code and called via FFI at codegen time;
    its logic is never reinterpreted into Python.
    """

    kind: Literal["formula"] = "formula"
    script: str | None = None


class ConstantNode(GraphNode):
    """A constant value. One output terminal (index 0)."""

    kind: Literal["constant"] = "constant"
    value: ScalarValue = None
    lv_type: LVType | None = None
    raw_value: str | None = None
    label: str | None = None
    # Raw printf-style numeric display-format string from the parser
    # (``ParsedConstant.display_format``, e.g. ``%.0x`` for hex), threaded
    # through unchanged — render/nodes.py is where it's interpreted.
    display_format: str | None = None


class LocalVariableNode(GraphNode):
    """A Local Variable (class="gRef") — reads or writes an FP control's
    value from a POSITION on the diagram, not a passthrough alias.

    One terminal: direction "output" when reading the control's current
    value, "input" when writing a new value to it (``is_write``).

    ``control_terminal_id`` is the referenced control's own FP terminal id
    (qualified), when the local variable's paramIdx resolved to a known
    front-panel control. It may be None if resolution failed (e.g. a
    global VI's local var, or an out-of-range index) — the node is still
    created (name falls back to "Local Variable") so its position/wires
    are never silently dropped.
    """

    kind: Literal["local_variable"] = "local_variable"
    control_name: str | None = None
    control_terminal_id: str | None = None
    is_write: bool = False


# Discriminated union of all node types
AnyGraphNode = (
    VINode | PrimitiveNode | StructureNode | ConstantNode | InPlaceNode
    | FormulaNode | LocalVariableNode | DisableStructureNode | EventStructureNode
)


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
    # DCO printf-style display spec (e.g. "%.0x" for hex), threaded from the
    # ConstantNode so describe/netlist render the LabVIEW radix like the renderer.
    display_format: str | None = None
    raw_value: str | None = None
    name: str | None = None
    # Structure containment (None = top-level diagram). Mirrors GraphNode:
    # parent = containing structure's node id, frame = selector value / index.
    parent: str | None = None
    frame: str | int | None = None


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


# ============================================================
# Class hierarchy types (docs: class landing pages + navigation)
# ============================================================


@dataclass
class ClassFieldEntry:
    """One private-data field on a class's Properties list.

    ``inherited`` distinguishes fields declared on this class (own, from the
    class's own private-data cluster) from fields declared on an ancestor
    class (inherited, present because LabVIEW nests the parent's private
    data cluster as the first field of the child's).
    """

    field: ClusterField
    inherited: bool


@dataclass
class ClassHierarchyInfo:
    """Hierarchy info for one loaded LabVIEW class.

    All names are fully-qualified (``"X.lvclass"``, or ``"Lib.lvlib:X.lvclass"``
    for library-nested classes). ``methods`` and ``child_classes`` only include
    entries that are themselves loaded/documented (no dangling links).
    """

    classname: str
    parent_class: str | None
    child_classes: list[str]
    methods: list[str]
    fields: list[ClassFieldEntry]


@dataclass
class MethodAccessInfo:
    """Access-scope info for a class method VI, from its owning class.

    ``scope`` is one of the scopes LabVIEW class items are parsed with today
    ("public" / "protected" / "private" — see ``structure.SCOPE_MAP``).
    """

    vi_name: str
    scope: str
    is_accessor: bool
    accessor_type: str | None
    accessor_field: str | None


@dataclass
class MethodOverrideInfo:
    """Bidirectional override links for a class method VI.

    ``overrides`` is the immediate parent class's same-named method (if it is
    itself a documented VI). ``overridden_by`` is every immediate child
    class's same-named method that is documented.
    """

    vi_name: str
    overrides: str | None
    overridden_by: list[str]

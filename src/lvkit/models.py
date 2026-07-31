"""Shared pipeline models — used by parser, graph, and codegen layers.

Two groups of types:
- Primitive / wiring types (dataclasses): LVType, EnumValue, ClusterField
- Flow types (Pydantic BaseModel): Terminal hierarchy, Tunnel, PropertyDef,
  Frame hierarchy, Operation hierarchy

The Frame ↔ Operation types are co-located here because they form a Pydantic
circular reference (Frame.operations → Operation, CaseOperation.frames →
CaseFrame) that must be resolved within a single module via model_rebuild().
The parser constructs CaseFrame/SequenceFrame instances directly, so the whole
cluster must live in a parser-importable module — not inside graph/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

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
    # Display-only label (resolved def's terminal name, e.g. "x"/"difference").
    # Separate from ``name`` because ``name`` drives codegen variable naming;
    # this must never change generated code. Used by the renderer's connector-
    # pane hover / tooltip and by describe. See graph/construction.py.
    display_name: str | None = None
    lv_type: LVType | None = None
    var_name: str | None = None  # set during codegen
    nmux_role: str | None = None  # "agg" or "list"
    nmux_field_index: int | None = None  # class field index
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    default_value: ScalarValue = None
    inverted: bool = False  # "Not" applied to this terminal (e.g. cpdArith)

    def python_type(self) -> str:
        """Python type string derived from lv_type."""
        return self.lv_type.to_python() if self.lv_type else "Any"

    @property
    def is_error_cluster(self) -> bool:
        """Check if this terminal carries a LabVIEW error cluster.

        Primary check: type-based (_is_error_cluster on lv_type).
        Fallback: name-based, but ONLY when the type is unknown or is a
        cluster without field metadata.  If the type is known to be
        non-cluster (array, primitive, etc.), the name is irrelevant —
        "error" in a terminal name does not make an Array an error cluster.

        Name matching uses word boundaries: "error in" / "error out" as
        phrases, not substring "in" inside arbitrary words like "pass*in*g".
        """
        if self.lv_type:
            if _is_error_cluster(self.lv_type):
                return True
            # Type is known and is NOT an error cluster — trust the type.
            # Only fall through to name heuristic when type is missing.
            return False
        # No type info — fall back to name heuristic (word-boundary match).
        name = (self.name or "").lower()
        if "no error" in name:
            return True
        return bool(
            re.search(r"\berror\s+(in|out)\b", name)
        )


def bundle_unbundle_name(
    terminals: list[Terminal], *, by_name: bool = False,
) -> str | None:
    """Human Bundle/Unbundle name from FIELD (``nmux_role=="list"``) terminal
    direction -- the ONE rule behind the whole cluster-mux family (nMux/mux/
    demux, the IPES cluster border node ``decomposeClusterNode``, ...): fields
    are INPUTS -> assembling a cluster (Bundle), fields are OUTPUTS -> taking
    one apart (Unbundle). The AGGREGATE (``nmux_role=="agg"``) terminal(s) are
    never counted. ``by_name`` selects the "By Name" wording (nMux /
    decomposeClusterNode access fields by name, not position). Returns None
    when there are no field terminals -- nothing to key the direction off;
    the caller decides the fallback (a static default name, or leaving an
    existing name untouched)."""
    field_terms = [t for t in terminals if t.nmux_role == "list"]
    if not field_terms:
        return None
    bundling = field_terms[0].direction == "input"
    if by_name:
        return "Bundle By Name" if bundling else "Unbundle By Name"
    return "Bundle" if bundling else "Unbundle"


class FPTerminal(Terminal):
    """A connector pane terminal on a VINode."""

    kind: Literal["fp"] = "fp"
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    is_indicator: bool = False
    is_public: bool = True
    control_type: str | None = None
    # The FRONT-PANEL DCO uid this BD terminal references — the BD↔FP bridge
    # (``fPTerm/<dco>@uid`` == the ``fPDCO@uid`` of the owning control). Unlike
    # ``id`` (which carries the BD ``fPTerm`` heap uid), this is the front-panel
    # OBJECT's identity, so it's the stable key for correlating "the same
    # control" across two versions of a VI — including a rename (same fp_dco_uid,
    # changed ``name``). See graph/diff.py's terminal correlation. None when the
    # BD terminal has no resolvable FP DCO (rare).
    fp_dco_uid: str | None = None
    default_value: ScalarValue = None
    enum_values: list[str] = []
    # Structure containment for a SPECIFIC on-diagram GLYPH of this control —
    # set only when the heap places this terminal inside a case/event/disable
    # frame or a stacked-sequence frame via an sRN's termList (a control used
    # as e.g. an Event Structure's registered event-source terminal — see
    # graph/construction.py's frame-attribution pass). None (the common case)
    # means no such frame-scoped placement was found — either the control is
    # genuinely VI-global, or it was never referenced via an sRN. FPTerminal
    # itself isn't a GraphNode (one FPTerminal is shared VI-wide, not one per
    # placement), so this can't just inherit GraphNode.parent/frame — it
    # mirrors that shape instead (see render/scene.py's frame-path lookup).
    parent: str | None = None
    frame: str | int | None = None


class TunnelTerminal(Terminal):
    """An outer or inner tunnel terminal on a StructureNode."""

    kind: Literal["tunnel"] = "tunnel"
    tunnel_type: str = ""  # "lSR", "rSR", "lpTun", "lMax", "caseSel"
    boundary: str = ""  # "outer" or "inner"
    paired_id: str | None = None  # matching terminal on other side
    # For case-structure inner tunnels: the owning CaseFrame's selector_value.
    # None for outer tunnels and for non-case-structure tunnels (loop/sequence).
    frame: str | int | None = None


class Tunnel(BaseModel):
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
# Frame types (Pydantic) — shared between graph nodes and codegen
# Must live with Operation due to Pydantic circular reference:
#   Frame.operations → Operation
#   CaseOperation.frames → CaseFrame
#   SequenceOperation.frames → SequenceFrame
# ============================================================


class SelectorRange(BaseModel):
    """One selector range a case frame matches, from SelectRangeArray32.

    A frame can match several: a single value (``start == end``, e.g. ``3``),
    a closed range (``start..end``, e.g. ``3..10``), or an open range
    (``open_start``/``open_end`` for ``..5`` / ``3..``). LabVIEW reconstructs
    the selector label from these live — there is no stored per-frame label —
    so we preserve them faithfully for the renderer to format by type
    (enum item names, quoted strings, ``a, b``, ``a..b``)."""

    start: int
    end: int
    open_start: bool = False  # "..end" — no lower bound
    open_end: bool = False    # "start.." — no upper bound

    @property
    def is_single(self) -> bool:
        return self.start == self.end and not self.open_start and not self.open_end


class Frame(BaseModel):
    """Base frame — common fields for any structure frame."""

    model_config = {"arbitrary_types_allowed": True}

    uid: str | None = None
    inner_node_uids: list[str] = []
    operations: list[Operation] = []


class CaseFrame(Frame):
    """A frame in a case structure — selected by a selector value."""

    selector_value: str | int = 0
    is_default: bool = False
    # Faithful selector ranges from SelectRangeArray32 (numeric/enum
    # selectors). Empty for the default frame, booleans, and strings —
    # there ``selector_value`` already holds the display token. The renderer
    # formats these against the selector's resolved type.
    selector_ranges: list[SelectorRange] = []
    # Faithful selector strings for STRING selectors (a frame can match
    # several, e.g. ``"jpe", "jpeg", "jpg"``). Empty for non-string frames and
    # the default frame — there ``selector_value``/``selector_ranges`` carry
    # the token. ``selector_value`` holds the first string as the unique key.
    selector_strings: list[str] = []


class SequenceFrame(Frame):
    """A frame in a flat or stacked sequence — executes in order."""

    index: int = 0


class EventFrame(Frame):
    """A frame in an Event Structure — handles ONE registered event.

    Unlike a case frame, the active frame is chosen at RUNTIME by whichever
    event actually fires — there is no fixed selector wire/value, so this
    carries a display ``event_label`` instead of ``selector_value``.

    LabVIEW stores a per-frame label only for the frame it last displayed in
    the editor (heap ``selString``/``dIdx``) — there is no per-frame label
    table like a case's dataspace ``SelectorTable``. That ONE frame's label
    is the faithful, LabVIEW-rendered text (e.g. ``[3] "Control": Value
    Change`` or ``[0] Timeout``, brackets included — LabVIEW's own
    convention). Every OTHER frame's label is RECONSTRUCTED from its
    ``EventSpec`` heap entry (source control caption + event-type name, when
    both are resolvable) in the same bracketed format; an unresolvable
    control or an unconfirmed event-type code degrades gracefully rather
    than fabricating a name (see parser/nodes/event.py).
    """

    event_label: str = ""


# ============================================================
# Codegen types (Pydantic) — consumed by code generation
# Converted from graph nodes by lvkit.graph (InMemoryVIGraph.get_operations())
# ============================================================


class Operation(BaseModel):
    """Base operation node for code generation."""

    model_config = {"arbitrary_types_allowed": True}

    id: str
    name: str | None
    labels: list[str]
    terminals: list[Terminal] = []
    node_type: str | None = None
    tunnels: list[Tunnel] = []
    inner_nodes: list[Operation] = []
    description: str | None = None
    poly_variant_name: str | None = None
    # Fully qualified on-disk path joined with /, e.g.
    # "<vilib>/Utility/error.llb/Error Cluster From Error Code.vi".
    # Set on SubVI call operations from the parser path_tokens. Always
    # None for primitives (they're identified by primResID, not by file)
    # and for structures (loops, cases, sequences). Used by resolution
    # diagnostics to point an LLM at the real source file.
    qualified_path: str | None = None


class PrimitiveOperation(Operation):
    """A primitive (Add, Subtract, etc.)."""

    primResID: int | None = None
    operation: str | None = None  # cpdArith: "add", "or"
    poser_uid: str | None = None  # Decompose/recompose pair UID (IPES)


class SubVIOperation(Operation):
    """A SubVI call."""


class PropertyOperation(Operation):
    """Property node read/write."""

    object_name: str | None = None
    object_method_id: str | None = None
    properties: list[PropertyDef] = []


class InvokeOperation(Operation):
    """Invoke node method call."""

    object_name: str | None = None
    object_method_id: str | None = None
    method_name: str | None = None
    method_code: int | None = None


class CaseOperation(Operation):
    """Case structure with selector-driven frames."""

    frames: list[CaseFrame] = []
    selector_terminal: str | None = None


class LoopOperation(Operation):
    """While or for loop."""

    loop_type: str | None = None
    stop_condition_terminal: str | None = None
    # While loop conditional-terminal polarity. True = Continue-if-True,
    # False = Stop-if-True (default). See ParsedLoopStructure for the
    # data evidence backing this polarity mapping.
    stop_condition_inverted: bool = False


class SequenceOperation(Operation):
    """Flat or stacked sequence."""

    frames: list[SequenceFrame] = []


class EventOperation(Operation):
    """Event Structure with one frame per registered event.

    Frame-bearing like a case (one subdiagram per frame), but the active
    frame is chosen at RUNTIME by whichever event fires -- there is no
    selector wire, unlike CaseOperation. Wired through
    describe/diff/netlist/render so its content stays visible. Codegen emits an
    explicit ``raise NotImplementedError`` (see codegen/nodes/event.py): an
    asynchronous UI event loop has no headless runtime analog, so it fails
    loudly rather than silently dropping the VI's event behaviour.
    """

    frames: list[EventFrame] = []


class DisableStructureOperation(Operation):
    """Diagram/Conditional Disable structure -- frame-bearing like a case,
    but the active frame is fixed at compile/edit time (no runtime
    selector_terminal -- unlike CaseOperation). Kept as a distinct type so it
    does NOT enter case codegen (a match/case over a selector that doesn't
    exist); codegen has no dedicated generator for it yet, so it falls
    through to the existing "unknown node type" comment fallback. Render
    draws it like a case (see render/scene.py, render/draw.py).
    """

    frames: list[CaseFrame] = []


class InPlaceOperation(Operation):
    """In Place Element Structure (decompose/recompose).

    IPES takes data in, decomposes at the input boundary (creating field
    access expressions), lets inner ops modify fields, then recomposes at
    the output boundary (writing fields back). Same data, no copies.

    decompose_ops and recompose_ops are boundary operations — they sit on
    the structure border like special tunnels, not inside the body.
    They do not go through generate_body().
    """

    decompose_ops: list[PrimitiveOperation] = []
    recompose_ops: list[PrimitiveOperation] = []


class FormulaOperation(Operation):
    """A Formula Node (fBox): an embedded C-like script over typed terminals.

    The ``script`` is compiled to native code and invoked via FFI in the
    generated Python. Terminals carry the script's input/output variables
    (name, LVType, direction) on the base Operation.
    """

    script: str | None = None


# Resolve forward references for self-referential types
Operation.model_rebuild()
Frame.model_rebuild()


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

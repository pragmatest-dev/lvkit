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
from enum import Enum
from typing import Literal

from pydantic import BaseModel

# Type alias for scalar constant/default values
ScalarValue = str | int | float | bool | None

# ============================================================
# Shared types (used by both graph and codegen layers)
# ============================================================


class LVTypeKind(str, Enum):
    """The family of a resolved ``LVType`` — its top-level discriminator.

    A CLOSED set: every ``LVType`` is built with one of these (an unrecognized
    LabVIEW type name collapses to ``PRIMITIVE`` in ``parser.type_mapping``); there
    is no "unknown" kind. ``(str, Enum)`` (not ``StrEnum`` — 3.11+) so a member IS
    its string and compares equal to a raw ``"primitive"`` literal drop-in — the
    ~40 construction sites and the comparisons all keep working.
    """

    PRIMITIVE = "primitive"
    ENUM = "enum"
    CLUSTER = "cluster"
    ARRAY = "array"
    RING = "ring"
    TYPEDEF_REF = "typedef_ref"
    CLASS = "class"


@dataclass
class LVType:
    """LabVIEW type structure - unified representation for all types."""

    kind: LVTypeKind
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
        if self.kind == LVTypeKind.PRIMITIVE:
            # Refnum with class name → use the class type
            if self.underlying_type == "Refnum" and self.classname:
                name = _sanitize_type_name(self.classname.replace(".lvclass", ""))
                return name or "Any"
            return _LV_TO_PYTHON_TYPE.get(self.underlying_type or "", "Any")
        elif self.kind == LVTypeKind.ARRAY:
            inner = self.element_type.to_python() if self.element_type else "Any"
            result = f"list[{inner}]"
            for _ in range((self.dimensions or 1) - 1):
                result = f"list[{result}]"
            return result
        elif self.kind == LVTypeKind.CLUSTER:
            if self.typedef_name:
                name = _sanitize_type_name(self.typedef_name)
                return name or "dict[str, Any]"
            return "dict[str, Any]"
        elif self.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            if self.typedef_name:
                name = _sanitize_type_name(self.typedef_name)
                return name or "int"
            return "int"
        elif self.kind == LVTypeKind.TYPEDEF_REF:
            if self.typedef_name:
                name = _sanitize_type_name(self.typedef_name)
                return name or "Any"
            return "Any"
        return "Any"

    def type_descriptor(self) -> str:
        """Render a LabVIEW-faithful type label from this type's structure.

        The FAITHFUL counterpart to :meth:`to_python` — every non-codegen
        surface (describe/netlist/diff/queries/docs) must use this, never
        ``to_python()``, per the project's clean-room LAW: LabVIEW's strict
        type system (enum members, cluster fields, refnum kinds, numeric
        representation) is load-bearing and must never be silently narrowed
        to a Python annotation outside the code generator.

        - ``array``: bracket-nested element label, one pair per dimension
          (``[DBL]``, ``[[I32]]``); an unresolved element renders ``?``.
        - ``enum``/``ring``: ``{member, member, …}`` in ORDINAL order (by
          ``EnumValue.value``), prefixed with the stripped typedef stem when
          named (``MethodEnum{setUp, testMethod, tearDown}``) or the base
          word when anonymous (``enum{...}`` / ``ring{...}``).
        - ``cluster``/``typedef_ref``: ``"error cluster"`` for an error
          cluster; otherwise ``Name{f1, f2}`` (named + fields), ``Name``
          (named, fields unknown), ``cluster{f1, f2}`` (fields, unnamed), or
          ``"cluster"`` (neither).
        - ``primitive``: a class refnum renders its class name verbatim
          (``TestCase.lvclass``); a parametrized refnum shows its element in
          braces like a cluster/enum (``Queue refnum{error cluster}``,
          ``Notifier refnum{DBL}``); otherwise ``"<ref_type> refnum"`` /
          ``"refnum"``. Other primitives map through the shared numeric/scalar
          token table, falling back to the raw ``underlying_type`` (never
          "Any"/"int") for an unmapped token.
        """
        if self.kind == LVTypeKind.ARRAY:
            dims = self.dimensions or 1
            inner = self.element_type.type_descriptor() if self.element_type else "?"
            return "[" * dims + inner + "]" * dims
        if self.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            members = (
                [
                    name
                    for name, _ev in sorted(
                        self.values.items(), key=lambda kv: kv[1].value
                    )
                ]
                if self.values
                else []
            )
            body = "{" + ", ".join(members) + "}"
            if self.typedef_name:
                return f"{_strip_typedef_stem(self.typedef_name)}{body}"
            return ("enum" if self.kind == LVTypeKind.ENUM else "ring") + body
        if self.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
            if _is_error_cluster(self):
                return "Error"
            fields = ", ".join(f.name for f in (self.fields or []))
            name = _strip_typedef_stem(self.typedef_name) if self.typedef_name else None
            if name and fields:
                return f"{name}{{{fields}}}"
            if name:
                return name
            if fields:
                return f"cluster{{{fields}}}"
            return "cluster"
        if self.kind == LVTypeKind.PRIMITIVE:
            if self.underlying_type == "Refnum":
                if self.classname:
                    return self.classname
                if self.ref_type:
                    # A parametrized refnum shows its element in braces, like a
                    # cluster/enum shows its members: ``Queue refnum{Error}``,
                    # ``Notifier refnum{DBL}``.
                    if self.element_type is not None:
                        inner = self.element_type.type_descriptor()
                        return f"{self.ref_type} refnum{{{inner}}}"
                    return f"{self.ref_type} refnum"
                return "refnum"
            return _SCALAR_TYPE_DESCRIPTOR.get(
                self.underlying_type or "", self.underlying_type or "?"
            )
        return "?"


@dataclass
class EnumValue:
    """A single value in an enum typedef."""

    value: int
    description: str | None = None


def enum_values_from_labels(labels: list[str]) -> dict[str, EnumValue]:
    """An ordered label list -> ``{label: EnumValue(ordinal)}``. The one place
    enum/ring members get their ordinals, shared by every type reconstructor
    (VCTP, CONP, FP-heap) so they can't drift."""
    return {label: EnumValue(value=i) for i, label in enumerate(labels)}


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
    if lv_type.kind not in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
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


# control_type (FP control class) -> LVTypeKind, for a terminal whose LVType
# didn't resolve. Composites map to their kind; every scalar/refnum/path control
# is a PRIMITIVE. An unrecognized control (or None) yields None -- an honest
# "unknown family", never a guessed default.
_CONTROL_FAMILY_KIND: dict[str, LVTypeKind] = {
    "stdClust": LVTypeKind.CLUSTER,
    "udClassDDO": LVTypeKind.CLASS,
    "indArr": LVTypeKind.ARRAY,
    "stdArray": LVTypeKind.ARRAY,
    "stdRing": LVTypeKind.RING,
    "stdEnum": LVTypeKind.ENUM,
    "stdRefNum": LVTypeKind.PRIMITIVE,
    "stdColorNum": LVTypeKind.PRIMITIVE,
    "stdNum": LVTypeKind.PRIMITIVE,
    "stdDBL": LVTypeKind.PRIMITIVE,
    "stdSGL": LVTypeKind.PRIMITIVE,
    "stdBool": LVTypeKind.PRIMITIVE,
    "stdString": LVTypeKind.PRIMITIVE,
    "stdPath": LVTypeKind.PRIMITIVE,
}


def _control_kind(control_type: str | None) -> LVTypeKind | None:
    """The type KIND for an unresolved terminal, from its FP control class, or
    None when the control is unrecognized/absent (honest unknown)."""
    if not control_type:
        return None
    return _CONTROL_FAMILY_KIND.get(control_type)


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

    def type_descriptor(self) -> str:
        """This terminal's exact type descriptor, or ``""`` when its type didn't
        resolve (``type_kind`` still identifies the family)."""
        return self.lv_type.type_descriptor() if self.lv_type else ""

    @property
    def type_kind(self) -> LVTypeKind | None:
        """The terminal's type KIND — from the resolved type, or derived from its
        FP control class when the type didn't resolve; None when genuinely
        unknown (never a guessed default)."""
        if self.lv_type is not None:
            return self.lv_type.kind
        return _control_kind(getattr(self, "control_type", None))

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
        return bool(re.search(r"\berror\s+(in|out)\b", name))


def bundle_unbundle_name(
    terminals: list[Terminal],
    *,
    by_name: bool = False,
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


def inplace_border_name(node_type: str, terminals: list[Terminal]) -> str | None:
    """Faithful per-tile name for the two IN-PLACE-ELEMENT-STRUCTURE border
    nodes that carry no field shape (unlike ``decomposeClusterNode``, which
    goes through :func:`bundle_unbundle_name`). Both halves of the pair share a
    node class; the READ (left) and WRITE (right) tile are told apart purely by
    which side the distinguishing terminal type sits on -- no guessing:

    - ``decomposeDataValRefNode`` -- the ``DataValueRef`` refnum is the INPUT on
      the read tile (it derefs the reference) and the OUTPUT on the write tile
      (it hands the reference back). -> ``"DVR Read"`` / ``"DVR Write"``.
    - ``decomposeArrayNode`` -- the read tile only indexes (array in, element
      out); the write tile replaces (array back OUT). An array-kind OUTPUT means
      the replace half. -> ``"Array Index"`` / ``"Array Replace"``.

    Returns ``None`` when the discriminating type isn't present (leave the
    node's fallback name untouched). Public NI docs: the border nodes are the
    'Data Value Reference Read / Write Element' and 'Array Index / Replace
    Elements' nodes -- these are the compact per-tile forms."""
    if node_type == "decomposeDataValRefNode":
        for t in terminals:
            lt = t.lv_type
            if lt is not None and lt.ref_type == "DataValueRef":
                return "DVR Read" if t.direction == "input" else "DVR Write"
        return None
    if node_type == "decomposeArrayNode":
        has_array_out = any(
            t.lv_type is not None
            and t.lv_type.kind == LVTypeKind.ARRAY
            and t.direction == "output"
            for t in terminals
        )
        return "Array Replace" if has_array_out else "Array Index"
    return None


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


class TunnelMode(str, Enum):
    """A loop tunnel's BASE aggregation mode -- ``dco class="lpTun"`` ONLY (a
    ``None`` ``Tunnel.mode`` means "not a loop tunnel", not "unknown"; every
    other tunnel kind -- lSR/rSR/lMax/csTun/seqTun/... -- never carries a
    mode). This is one of LabVIEW's two independent tunnel axes; the second is
    the orthogonal ``Tunnel.conditional`` modifier (see there). Decoded from
    the lpTun dco's own child elements (see ``parser/nodes/base.py::
    _lp_tun_mode``):

    - INDEXING: auto-index / auto-aggregate is ENABLED -- read directly from
      the ``innerLpTunDCO``'s ``objFlags`` bit ``0x400000`` (a second, older
      encoding uses an explicit ``<TunnelType>``). On an OUTPUT tunnel this
      builds an array element-by-element (element inner -> array outer); on an
      INPUT tunnel it indexes the array (array outer -> element inner). Same
      flag, opposite directions -- LabVIEW calls both "indexing".
    - LAST_VALUE: OUTPUT tunnel with indexing DISABLED (``innerLpTunDCO``
      present, index bit clear) -- passes only the FINAL iteration's value.
    - CONCATENATING: ``<TunnelType>02</TunnelType>`` -- concatenates all
      iterations' inputs into one array of the same dimension (rare).
    - PASSTHROUGH: INPUT tunnel with indexing disabled, or a plain non-loop
      pass-through -- same value in and out every iteration.

    The ``CONDITIONAL`` menu item is NOT a mode -- it is the orthogonal
    ``Tunnel.conditional`` flag that layers onto ANY output base mode
    (conditional last-value / conditional indexing / conditional concatenate).
    """

    INDEXING = "INDEXING"
    LAST_VALUE = "LAST_VALUE"
    CONCATENATING = "CONCATENATING"
    PASSTHROUGH = "PASSTHROUGH"


class TunnelTerminal(Terminal):
    """An outer or inner tunnel terminal on a StructureNode."""

    kind: Literal["tunnel"] = "tunnel"
    tunnel_type: str = ""  # "lSR", "rSR", "lpTun", "lMax", "caseSel"
    boundary: str = ""  # "outer" or "inner"
    paired_id: str | None = None  # matching terminal on other side
    # For case-structure inner tunnels: the owning CaseFrame's selector_value.
    # None for outer tunnels and for non-case-structure tunnels (loop/sequence).
    frame: str | int | None = None
    # Carried straight from the parsed Tunnel (construction.py) so
    # graph/operations.py::_tunnels_from_terminals can round-trip them onto
    # the rebuilt Tunnel -- see Tunnel.mode/sr_initialized/sr_stack_depth for
    # what each means and which tunnel_type populates it.
    mode: TunnelMode | None = None
    conditional: bool = False
    sr_initialized: bool | None = None
    sr_stack_depth: int | None = None


class Tunnel(BaseModel):
    """A tunnel connecting structure outer/inner terminals."""

    outer_terminal_uid: str
    inner_terminal_uid: str
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax", "caseSel", etc.
    paired_terminal_uid: str | None = None
    # Loop-tunnel BASE aggregation mode -- populated ONLY for ``tunnel_type ==
    # "lpTun"`` (see TunnelMode); None for every other tunnel_type.
    mode: TunnelMode | None = None
    # Orthogonal "Conditional" modifier (LabVIEW's Conditional menu item) --
    # True when an OUTPUT tunnel aggregates/keeps a value only on iterations
    # where a per-iteration boolean holds. Layers onto ANY base mode (see
    # TunnelMode); read from the lpTun dco's ``<IsConditional>True``. False for
    # non-conditional and for input tunnels (indexing has no conditional).
    conditional: bool = False
    # Shift-register STRUCTURE, not bits -- populated ONLY on the matching
    # tunnel_type, None on every other one:
    # - sr_initialized: True when this lSR tunnel's outer terminal carries an
    #   external wire (a value feeding the register before the loop starts).
    #   Set on tunnel_type == "lSR" tunnels only.
    sr_initialized: bool | None = None
    # - sr_stack_depth: this rSR's shift-register depth (len(lsrDCOList)) --
    #   1 for a normal (unstacked) shift register, N for an N-deep stacked
    #   one. Set on tunnel_type == "rSR" tunnels only.
    sr_stack_depth: int | None = None


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
    open_end: bool = False  # "start.." — no upper bound

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

    # Positional frame index (order in the Event Structure's frame list). The
    # active frame is chosen at runtime, so there is no selector_value to key
    # on — this position IS the stable correlation token, and it matches what
    # the renderer stamps (``construction.py`` → ``str(idx)``), so diff
    # frame-paths line up with the SVG's ``data-path`` for cross-pane lookup.
    index: int = 0
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
    label: str | None = None  # LabVIEW label (partID 16), see extract_label
    caption: str | None = None  # LabVIEW caption (partID 82), see extract_caption
    # The node's classification kind, e.g. "vi"/"loop"/"constant".
    kind: str = "operation"
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
    # DISPLAY ownership chain (owning .lvlib/.lvclass, outermost first) from the
    # VI's own ``<LIBN>`` — for a SubVI-call op it's the CALLEE's chain. Lets a
    # label read ``Class.lvclass:method`` to disambiguate same-named methods of
    # different classes; never a resolution key. Empty for primitives/structures.
    owning_libraries: list[str] = []

    @property
    def display_name(self) -> str:
        """Shared "best human label" convenience — NOT a mandate every
        consumer must route through (each view is still free to compose its
        own display from the truth fields below when it needs to). Precedence
        is IDENTITY-FIRST:
        ``<qualified identity> or name or caption or label or
        get_display_name(node_type)``.

        The codegen/resolution identity ``name`` wins — qualified with
        ``owning_libraries`` (from the callee's own ``<LIBN>``) as
        ``…lvlib:Class.lvclass:method`` when both are present, so two classes'
        same-named methods stay distinct. Only an identity-LESS node (a
        structure or constant, ``name is None``) falls back to its OWN author
        text ``caption or label`` (partID 82/16) — which is why a labeled loop
        reads its label but a subVI keeps its qualified identity, not a
        decorative node label. ``get_display_name(node_type)`` is the final
        fallback: the one human type-word table, so an unlabeled, unnamed
        structure (loop/case/sequence/event/in-place/disable) still reads e.g.
        "For Loop" instead of the raw XML class.

        Deferred import: ``lvkit.parser`` imports ``lvkit.models`` (Frame/
        Operation live here so the parser can construct them directly — see
        module docstring), so importing the parser back at module scope here
        would be circular.
        """
        from .parser.node_types import get_display_name

        if self.owning_libraries and self.name:
            return ":".join([*self.owning_libraries, self.name])
        if self.name:
            return self.name
        if self.caption:
            return self.caption
        if self.label:
            return self.label
        return get_display_name(self.node_type) if self.node_type else "node"


class PrimitiveOperation(Operation):
    """A primitive (Add, Subtract, etc.)."""

    primResID: int | None = None
    operation: str | None = None  # cpdArith: "add", "or"
    poser_uid: str | None = None  # Decompose/recompose pair UID (IPES)


class FeedbackOperation(Operation):
    """A LabVIEW Feedback Node (z^-N state element) -- a standalone Gated-SSA
    mu, exactly like a loop shift register: it carries a value from one loop
    iteration (or VI call) to the next.

    Serialized as a MASTER/SLAVE pair (see parser ``FeedbackNode``). The
    MASTER (``is_master=True``, class ``hiddenFBNode``) owns the OUTPUT (read)
    and INITIALIZER terminals; the SLAVE (``is_master=False``, class
    ``slaveFBInputNode``) owns the single INPUT (written value) terminal.
    ``partner_uid`` is the fully qualified id of the other side; ``delay`` is
    the z^-N depth (``feedbackNodeDelay``), present on the master only. The
    netlist projects the master as one ``fb{k}`` mu net and dissolves the
    slave into it (its written value becomes the mu ``recur``). Codegen is not
    yet supported and fails loud.
    """

    is_master: bool = True
    partner_uid: str | None = None
    delay: int | None = None


class SubVIOperation(Operation):
    """A SubVI call."""


class PropertyOperation(Operation):
    """Property node read/write."""

    object_name: str | None = None
    object_method_id: str | None = None
    properties: list[PropertyDef] = []
    # ``properties[i]`` correlates to the terminal whose id is
    # ``value_terminal_ids[i]`` (matches a ``terminals[j].id``). See
    # ``graph.op_walk.correlate_property_terminals`` and
    # ``graph.models.PrimitiveNode.property_value_terminal_ids`` for why this
    # is a structural (dcoList-based) correlation, not a type-based guess.
    value_terminal_ids: list[str] = []


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
    # For-loop parallelism (LabVIEW's "Configure Parallelism..." dialog) --
    # True when the forLoop element has a child <ParForWorkers>. Always False
    # for while loops (they have no such element). See ParsedLoopStructure.
    parallel: bool = False
    # <ParForNumStaticWorkers> (hex text, e.g. "08" -> 8), when set and
    # nonzero; None otherwise (absent, or "00" -- no static worker count
    # configured). <ParForIndexDistribution> is deliberately NOT modeled --
    # "00" in every corpus occurrence, so its other values are unconfirmed.
    parallel_static_workers: int | None = None


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

# LabVIEW's conventional short token for the numeric family + Boolean/Path —
# shared by the renderer's glyph labels (render/style.py's ``_TYPE_REPR``,
# terminal-icon text like a wire tooltip's "DBL"/"I32" box) AND
# ``LVType.type_descriptor()``'s faithful text label. These read identically in
# both contexts; String/Variant diverge (a glyph reads "abc"/"Var", a
# faithful text label reads the full word) and stay defined separately in
# each consumer — see ``_SCALAR_TYPE_DESCRIPTOR`` below and ``render/style.py``.
_NUMERIC_TYPE_DESCRIPTOR: dict[str, str] = {
    "NumFloat64": "DBL",
    "NumFloat32": "SGL",
    "NumFloatExt": "EXT",
    "NumComplex64": "CSG",
    "NumComplex128": "CDB",
    "NumComplexExt": "CXT",
    "NumInt8": "I8",
    "NumInt16": "I16",
    "NumInt32": "I32",
    "NumInt64": "I64",
    "NumUInt8": "U8",
    "NumUInt16": "U16",
    "NumUInt32": "U32",
    "NumUInt64": "U64",
    "Boolean": "TF",
    "Path": "Path",
}

# The full faithful-label scalar map used by ``LVType.type_descriptor()`` — the
# shared numeric/Boolean/Path table plus the non-numeric tokens spelled out
# in full (a text label reads "String"/"Variant", never the glyph
# abbreviation "abc"/"Var" — see ``render/style.py::_TYPE_REPR`` for that).
_SCALAR_TYPE_DESCRIPTOR: dict[str, str] = {
    **_NUMERIC_TYPE_DESCRIPTOR,
    "String": "String",
    "Variant": "Variant",
    "LVVariant": "Variant",
}

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
        "stdPath": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path"),
        "stdString": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        "stdBool": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean"),
        "stdNum": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
        "stdDBL": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
        "stdI32": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
        "stdI16": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt16"),
        "stdU32": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32"),
        "stdU16": LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16"),
    }
    return mapping.get(control_type)


def _sanitize_type_name(typedef_name: str) -> str:
    """Sanitize a typedef name into a valid Python identifier."""
    name = typedef_name.split(":")[-1].replace(".ctl", "")
    name = "".join(c for c in name if c.isalnum() or c == "_")
    return name


def _strip_typedef_stem(typedef_name: str) -> str:
    """The type's own display name from a possibly library-qualified
    ``typedef_name``: drop everything before the last ``:`` qualifier and
    strip a ``.ctl`` control-file extension. UNLIKE ``_sanitize_type_name``,
    this is never mangled into a Python identifier — it's a LabVIEW label
    (``type_descriptor()``), not a codegen type name, so spaces/punctuation in the
    type's own name are kept verbatim (mirrors the stripping convention in
    ``graph/construction.py``'s ``_format_lv_type_for_display``)."""
    return typedef_name.rsplit(":", 1)[-1].replace(".ctl", "")

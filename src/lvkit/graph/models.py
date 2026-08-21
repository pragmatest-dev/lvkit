"""Graph node and codegen context models.

These types are produced by the graph layer and consumed by codegen.
Parser never imports from this module.

Dependency: lvkit.models (shared primitives + flow types)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
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
    label: str | None = None  # LabVIEW label (partID 16), see extract_label
    caption: str | None = None  # LabVIEW caption (partID 82), see extract_caption
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
    # The connector-pane PATTERN number (FPHb ``<conPane><conId>``) — the key
    # into ``connector_pane_geometry`` for the cell grid. None when the
    # VI has no connector pane parsed. LabVIEW stores only this id, not the grid.
    connector_pattern_id: int | None = None
    # The VI's ownership chain from its own ``<LIBN>`` (owning .lvlib/.lvclass,
    # outermost first). On a SubVI-CALL node it's the CALLEE's chain (copied when
    # the callee resolves). DISPLAY only — a ``chain:name`` label that
    # disambiguates two classes' same-named methods; never a resolution key.
    owning_libraries: list[str] = []
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
    # Property node only: qualified terminal ids from the parser's dcoList,
    # ``properties[i]`` correlates to the terminal whose id is
    # ``property_value_terminal_ids[i]`` -- LabVIEW's real dcoList/permDCOList
    # structural split (the object reference in/out and error in/out ALWAYS
    # live in permDCOList, never dcoList, regardless of their own TYPE). This
    # is exact even when a property's value is itself Refnum-typed (e.g. a
    # "Library:Project" property returns a Project reference) -- a type-based
    # Refnum/error-cluster filter can't tell that apart from the object
    # reference terminal, but this structural id list always can. See
    # ``op_walk.correlate_property_terminals``.
    property_value_terminal_ids: list[str] = []


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
    # For-loop parallelism + static worker count -- see
    # ParsedLoopStructure.parallel / .parallel_static_workers.
    parallel: bool = False
    parallel_static_workers: int | None = None
    # Border-terminal KINDS ("i"/"N"/"cond") hidden via LabVIEW's "Visible
    # Items" (objFlags bit 0x800000 on the inner sRN term). The renderer omits
    # a hidden terminal's glyph; a future viewer toggle can reveal them. Empty
    # when all are shown. See ParsedLoopStructure.hidden_border_terminals.
    hidden_border_terminals: frozenset[str] = frozenset()


class SequenceNode(StructureNode):
    """A flat or stacked sequence with ordered frames."""

    model_config = {"arbitrary_types_allowed": True}

    frames: list[SequenceFrame] = []
    # Frame a STACKED sequence last displayed (heap ``dIdx``, range-checked) —
    # the faithful initial view. None for a flat sequence (all frames shown)
    # and when ``dIdx`` is an out-of-range legacy ordinal → renderer opens
    # frame 0.
    displayed_frame: int | None = None


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
    # time (heap ``activeDiag``). None if unresolved.
    active_frame: int | None = None
    # Frame LabVIEW last displayed (heap ``dIdx``, range-checked) -- the saved
    # visible frame, which for a Conditional Disable can differ from the enabled
    # one. Preferred as the initial view when present; None for an out-of-range
    # legacy ``dIdx`` → renderer falls back to Enabled/active_frame. See #30.
    displayed_frame: int | None = None


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
    # ``label`` inherited from GraphNode (partID 16) — this IS the constant's
    # display text; codegen never treats a constant's label as an identity.
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
    VINode
    | PrimitiveNode
    | StructureNode
    | ConstantNode
    | InPlaceNode
    | FormulaNode
    | LocalVariableNode
    | DisableStructureNode
    | EventStructureNode
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
    parent_kind: str | None = None


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
        from_parent_kind: str | None = None,
        to_parent_kind: str | None = None,
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
                parent_kind=from_parent_kind,
            ),
            dest=WireEnd(
                terminal_id=to_terminal_id,
                node_id=to_parent_id or to_terminal_id,
                index=to_slot_index,
                name=to_parent_name,
                parent_kind=to_parent_kind,
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
    def from_parent_kind(self) -> str | None:
        return self.source.parent_kind

    @property
    def to_parent_kind(self) -> str | None:
        return self.dest.parent_kind

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
    label: str | None = None  # LabVIEW label (partID 16), the author's caption
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


class TypedefStatus(Enum):
    """VI Structure -> type-definition kind (the LVSR ``TypeDefVI``/
    ``StrictTypeDefVI`` pair, NI-doc + corpus verified).

    Derived from ``<Execution TypeDefVI>``/``<Execution StrictTypeDefVI>`` --
    see ``parser.metadata._parse_lvsr_properties``: ``(0, 0)`` ->
    not_a_typedef, ``(1, 0)`` -> typedef, ``(1, 1)`` -> strict_typedef.
    ``(0, 1)`` never occurs.
    """

    NOT_A_TYPEDEF = "not_a_typedef"
    TYPEDEF = "typedef"
    STRICT_TYPEDEF = "strict_typedef"


class Priority(Enum):
    """VI Properties -> Execution -> Priority (the LVSR ``Priority``
    attribute -- a 0-indexed FILE FORMAT code, NOT the VI-Server scripting
    enum, NI-doc + corpus verified).

    Derived from ``<Execution Priority="N">``: 0=background, 1=normal,
    2=above_normal, 3=high, 4=time_critical, 5=subroutine (also implies the
    legacy ``IsSubroutine`` flag -- redundant, so the parser stops reading
    it). See ``parser.metadata._parse_lvsr_properties``.
    """

    BACKGROUND = "background"
    NORMAL = "normal"
    ABOVE_NORMAL = "above_normal"
    HIGH = "high"
    TIME_CRITICAL = "time_critical"
    SUBROUTINE = "subroutine"


class Reentrancy(Enum):
    """VI Properties -> Execution -> Reentrant execution (the LVSR
    ``IsReentrant``/``PooledReentrancy`` pair, NI-doc + corpus verified).

    Derived from ``<Execution IsReentrant>``/``<Execution PooledReentrancy>``:
    ``IsReentrant=0`` -> non_reentrant (``PooledReentrancy`` is then IGNORED
    -- a sticky leftover bit when non-reentrant); ``IsReentrant=1`` +
    ``Pooled=0`` -> preallocated_clone; ``IsReentrant=1`` + ``Pooled=1`` ->
    shared_clone. See ``parser.metadata._parse_lvsr_properties``.
    """

    NON_REENTRANT = "non_reentrant"
    SHARED_CLONE = "shared_clone"
    PREALLOCATED_CLONE = "preallocated_clone"


class ExecSystem(Enum):
    """VI Properties -> Execution -> Preferred execution system (the LVSR
    ``PrefExecSyst`` attribute, NI-doc + corpus verified).

    Derived from ``<Execution PrefExecSyst="N">``: -1=same_as_caller,
    0=user_interface, 1=standard, 2=instrument_io, 3=data_acquisition,
    4=other_1, 5=other_2. Any other/absent value -> same_as_caller (the
    default). See ``parser.metadata._parse_lvsr_properties``.
    """

    SAME_AS_CALLER = "same_as_caller"
    USER_INTERFACE = "user_interface"
    STANDARD = "standard"
    INSTRUMENT_IO = "instrument_io"
    DATA_ACQUISITION = "data_acquisition"
    OTHER_1 = "other_1"
    OTHER_2 = "other_2"


class LockState(Enum):
    """VI Properties -> Protection tri-state (the LabVIEW VI Properties
    dialog's Protection page has exactly these three states — verified
    against NI docs).

    Derived from the main XML's ``<LVSR>`` block: ``Library[@Protected]``
    (the LVSR ``<Library>``, NOT the ``<LIBN>`` owning-library name) plus
    whether a real (non-empty, non-placeholder) BD ``<Password Hash>`` is
    present — see ``parser.metadata._parse_lvsr_properties``. There is no
    unlocked-with-password state.
    """

    UNLOCKED = "unlocked"
    LOCKED = "locked"
    PASSWORD_PROTECTED = "password_protected"


@dataclass
class ExecutionProps:
    """VI Properties -> Execution page, from ``<Execution>`` unless noted.

    ``inline``/``inlinable``/``auto_error_handling``/``always_calls_parent``
    come from ``<Execution2>``; ``allow_debugging``/``print_after_exec`` come
    from ``<Instrument>`` (verified against a real extracted main .xml).

    ``priority``/``reentrancy``/``exec_system`` are FAITHFUL enums (see
    ``Priority``/``Reentrancy``/``ExecSystem`` above) -- the legacy
    ``IsSubroutine`` flag is redundant with ``priority == SUBROUTINE`` and is
    no longer read.
    """

    priority: Priority = Priority.NORMAL  # Priority
    reentrancy: Reentrancy = Reentrancy.NON_REENTRANT  # IsReentrant + PooledReentrancy
    exec_system: ExecSystem = ExecSystem.SAME_AS_CALLER  # PrefExecSyst
    run_when_opened: bool = False  # RunOnOpen
    show_fp_when_loaded: bool = False  # ShowFPOnLoad
    show_fp_when_called: bool = False  # ShowFPOnCall
    close_fp_after_call: bool = False  # CloseAfterCall
    auto_preallocate_arrays: bool = False  # AllowAutoPrealloc
    inline: bool = False  # ShouldInline (<Execution2>)
    inlinable: bool = False  # InlinableDiagram (<Execution2>)
    auto_error_handling: bool = False  # DefaultErrorHandling (<Execution2>)
    allow_debugging: bool = False  # DebugCapable (<Instrument>)
    always_calls_parent: bool = False  # AlwaysCallsParent (<Execution2>)
    print_after_exec: bool = False  # PrintAfterExec (<Instrument>)


@dataclass
class WindowProps:
    """VI Properties -> Window Appearance, from ``<FrontPanel>`` unless
    noted. ``auto_handle_menus`` comes from ``<Flags0C>``; the
    ``can_*``/``transparent`` fields come from ``<Flags12>``.
    """

    show_title_bar: bool = False  # ShowTitleBar
    show_menu_bar: bool = False  # ShowMenuBar
    show_toolbar: bool = False  # ToolBarVisible
    # ShowScrollBar: raw int, kept VERBATIM -- NOT remodeled. Really TWO
    # checkboxes (horizontal/vertical scroll bar): 3=both (default), 0=none,
    # 2=one. The bit<->axis mapping is unverified (insufficient corpus
    # samples showing every combination), so this stays the raw LVSR value
    # rather than a guessed enum/pair of bools.
    show_scrollbar: int | None = None  # ShowScrollBar
    auto_center: bool = False  # AutoCenter
    size_to_screen: bool = False  # SizeToScreen
    no_runtime_popup_menu: bool = False  # NoRuntimePopUp
    scale_with_window: bool = False  # ScaleProportn
    mark_return_button: bool = False  # MarkReturnBtn
    auto_handle_menus: bool = False  # AutoHndlMenus (<Flags0C>)
    can_close: bool = False  # WndCanClose (<Flags12>)
    can_resize: bool = False  # WndCanResize (<Flags12>)
    can_minimize: bool = False  # WndCanMinimize (<Flags12>)
    transparent: bool = False  # WndTransparent (<Flags12>)


@dataclass
class ToolbarProps:
    """VI Properties -> Window Appearance -> hidden toolbar buttons, from
    ``<ButtonsHidden>``. Only the three NAMED attributes -- the rest of
    ``<ButtonsHidden>`` is unnamed ``ViBhBitN`` heap bits, deliberately
    skipped (see ``VIProperties`` docstring).
    """

    hide_run_button: bool = False  # RunButton
    hide_abort_button: bool = False  # AbortButton
    hide_free_run_button: bool = False  # FreeRunButton


@dataclass
class InstanceProps:
    """VI Properties instance/poly-VI flags, from ``<Execution2>``."""

    is_system_vi: bool = False  # SystemVI
    show_poly_selector: bool = False  # ShowPolySelector
    hide_instance_caption: bool = False  # HideInstanceVICaption
    draw_instance_icon: bool = False  # DrawInstanceIcon
    remote_panel: bool = False  # RemotePanel


@dataclass
class KindProps:
    """What ROLE the user made this VI. All user-configured, but set
    outside the LVSR VI-Properties dialog (control-editor Type Def dropdown,
    connector-pane Dynamic Dispatch, save options) -- so grouped as its own
    sub-struct alongside Execution/Window/Toolbar/Instance. (Formerly part
    of the mislabeled ``VIStructure`` facet.)
    """

    # TypeDefVI + StrictTypeDefVI
    typedef_status: TypedefStatus = TypedefStatus.NOT_A_TYPEDEF
    dynamic_dispatch: bool = False  # DynamicDispatch
    source_only: bool = False  # SourceOnly (<Execution2>)
    has_no_block_diagram: bool = False  # HasNoBD
    is_instance_vi: bool = False  # InstanceVI (<Execution2>)


@dataclass
class VIHealth:
    """Emergent compile-health STATE -- the ONLY non-choice VI facet (all
    False for a healthy VI). A SIBLING facet to ``VIProperties`` (graph
    ``_vi_health`` / ``VIContext.health``), never nested. Was
    ``VIStructure``; its kind fields moved into ``VIProperties.kind``.
    """

    bad_node: bool = False  # BadNode
    bad_subvi: bool = False  # BadSubVI
    bad_subvi_link: bool = False  # BadSubVILink
    bad_compile: bool = False  # BadCompile
    broken_poly: bool = False  # BrokenPolyVI

    @property
    def is_broken(self) -> bool:
        return any(
            [
                self.bad_node,
                self.bad_subvi,
                self.bad_subvi_link,
                self.bad_compile,
                self.broken_poly,
            ]
        )


@dataclass
class VIProperties:
    """VI Properties dialog settings read from the main XML's ``<LVSR>``
    block -- comprehensive named flags, grouped to match the dialog's own
    pages. Only unnamed heap bits (``ViBhBitN``/``InStBitN``/``ViFpBitN``/
    ``WndBitN``/``WndFloatUnk*``) and opaque transient masks (``*Mask``/
    ``UndoRedo*``/watermarks/``State``) are excluded.

    ``kind`` (what ROLE the VI plays -- see ``KindProps``) is a sub-struct
    here, like execution/window/toolbar/instance. Compile-HEALTH (an
    emergent STATE, not a choice) is the separate ``VIHealth`` facet -- a
    sibling to this whole dataclass, never nested inside it.
    """

    # "Major.Minor.Bugfix", e.g. "21.0.0" -- from <Version Major Minor Bugfix>.
    lv_version: str | None = None
    # <Instrument Type="..."> verbatim, kept RAW -- NOT remodeled. pylabview
    # PARTIALLY decodes this (Control/Subsystem/Polymorph...) but also emits
    # un-decoded ordinals like "11" outside NI's documented VI-Type 0-9
    # range; a faithful enum needs a pylabview-source decode (follow-up), so
    # this stays the raw string as pylabview emits it.
    vi_type: str | None = None
    lock_state: LockState = LockState.UNLOCKED
    execution: ExecutionProps = field(default_factory=ExecutionProps)
    window: WindowProps = field(default_factory=WindowProps)
    toolbar: ToolbarProps = field(default_factory=ToolbarProps)
    instance: InstanceProps = field(default_factory=InstanceProps)
    kind: KindProps = field(default_factory=KindProps)


def bool_str(v: bool) -> str:
    """Lowercase display form of a bool VI-Properties/VIHealth flag value
    (``"false"``/``"true"``) -- NOT Python's ``str(bool)`` (``"False"``/
    ``"True"``), matching the netlist header's own lowercase flag syntax
    (see ``docs/reference/netlist.md`` "VI properties & structure header").
    The ONE shared formatter for a curated flag's old/new value, e.g. in
    ``diff._diff_vi_properties``/``_diff_vi_health``."""
    return "true" if v else "false"


# Curated boolean ``ExecutionProps`` (VI Properties -> Execution page) flags
# -> display label -- the ONE canonical set of high-signal BOOLEAN settings
# that actually change VI behaviour. NOT every ``ExecutionProps`` field --
# window/toolbar cosmetics are deliberately excluded noise (see
# ``VIProperties``'s own docstring); ``priority``/``reentrancy``/
# ``exec_system`` are ENUM-valued (like ``lock_state``) and are handled
# separately everywhere -- an enum transition in diff, an enum row in
# describe/the popover -- NEVER folded into this bool-flag map. Drives
# ``diff.py``'s ``_PROPERTY_BOOL_FIELDS`` and the render-viewer's status
# chips (``render/properties_panel.py``) -- one edit changes both surfaces.
CURATED_PROPERTY_FLAGS: dict[str, str] = {
    "run_when_opened": "run-on-open",
}

# Curated boolean ``KindProps`` (``VIProperties.kind``) flags -> display
# label, matching the netlist header's own flag vocabulary
# (``docs/reference/netlist.md`` "VI properties & structure header"). Drives
# ``diff.py``'s ``_KIND_BOOL_FIELDS`` and the render-viewer's status chips --
# one edit changes both surfaces. ``typedef_status`` is handled separately
# everywhere (an enum transition, not a bool flag).
CURATED_KIND_FLAGS: dict[str, str] = {
    "dynamic_dispatch": "dynamic-dispatch",
    "source_only": "source-only",
    "has_no_block_diagram": "no-block-diagram",
    "is_instance_vi": "instance-vi",
}

# Curated boolean ``VIHealth`` flags -> display label, matching the netlist
# header's own flag vocabulary (``docs/reference/netlist.md`` "VI properties
# & structure header"). Drives ``diff.py``'s ``_HEALTH_BOOL_FIELDS`` and the
# render-viewer's status chips -- one edit changes both surfaces.
CURATED_HEALTH_FLAGS: dict[str, str] = {
    "is_broken": "broken",
}


def vi_properties_to_dict(p: VIProperties) -> dict:
    """Canonical JSON shape for a ``VIProperties``: full nested groups
    (``execution``/``window``/``toolbar``/``instance``/``kind``, each a
    faithful ``dataclasses.asdict`` -- they have no nested Enum/dataclass
    fields of their own EXCEPT ``execution``'s ``priority``/``reentrancy``/
    ``exec_system`` and ``kind``'s ``typedef_status``), with every Enum field
    (``lock_state`` plus those four) unwrapped to its string value
    (``dataclasses.asdict`` alone wouldn't convert them). The ONE converter
    both the render viewer's SVG data attrs (``render/__init__.py``'s
    ``_vi_properties_data_attrs``) and the netlist JSON IR (``netlist.py``'s
    ``netlist_to_dict``) call, so the two surfaces can't drift into
    different shapes."""
    d = dataclasses.asdict(p)
    d["lock_state"] = p.lock_state.value
    d["execution"]["priority"] = p.execution.priority.value
    d["execution"]["reentrancy"] = p.execution.reentrancy.value
    d["execution"]["exec_system"] = p.execution.exec_system.value
    d["kind"]["typedef_status"] = p.kind.typedef_status.value
    return d


def vi_health_to_dict(h: VIHealth) -> dict:
    """Canonical JSON shape for a ``VIHealth``: every field, PLUS the
    derived ``is_broken`` property (``dataclasses.asdict`` alone omits it --
    it isn't a dataclass field). The ONE converter both the render viewer's
    SVG data attrs and the netlist JSON IR call."""
    d = dataclasses.asdict(h)
    d["is_broken"] = h.is_broken
    return d


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
    # User-settable VI Properties (Protection/Execution/…/Kind) from <LVSR>.
    properties: VIProperties = VIProperties()
    # Compile-health (emergent state, not a user setting) -- a SIBLING facet
    # to ``properties``, never nested inside it.
    health: VIHealth = VIHealth()
    # Connector-pane PATTERN number (FPHb conId) — key into the pattern-geometry
    # table for a faithful pane render. None when the VI has no connector pane.
    connector_pattern_id: int | None = None
    # The VI's own documentation text (STRG/DSTM), including any callee-propagated
    # description — the hover-help body. See VINode.description.
    description: str | None = None


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
    """VI metadata from main XML -- IDENTITY only (library/qualified_name/
    owning_libraries/description). VI Properties/VIHealth are separate,
    sibling name-keyed facets on the graph (``_vi_properties``/
    ``_vi_health``), NOT fields here -- see ``InMemoryVIGraph.__init__``.
    """

    library: str | None = None
    qualified_name: str | None = None
    # Ownership chain from ``<LIBN>`` (owning .lvlib/.lvclass, outermost first) —
    # for a DISPLAY class-qualified name; see VINode.owning_libraries.
    owning_libraries: list[str] = field(default_factory=list)
    # The VI's own documentation text (STRG/DSTM), shown in the hover help box.
    description: str | None = None


# ============================================================
# Source/Destination info types (returned by context queries)
# ============================================================


@dataclass
class SourceInfo:
    """Source terminal info from an incoming edge."""

    src_terminal: str
    src_parent_id: str
    src_parent_name: str | None = None
    src_parent_kind: str | None = None
    src_slot_index: int | None = None


@dataclass
class DestinationInfo:
    """Destination terminal info from an outgoing edge."""

    dest_terminal: str
    dest_parent_id: str
    dest_parent_name: str | None = None
    dest_parent_kind: str | None = None
    dest_slot_index: int | None = None


# ============================================================
# Query result types (returned by graph queries)
# ============================================================


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
    ("public" / "protected" / "private" / "community" — see
    ``structure.SCOPE_MAP``).

    ``is_static`` / ``must_override`` / ``must_call_parent`` mirror
    ``structure.LVMethod``'s same-named fields — carried onto the "owns" edge
    at load time (``loading.load_lvclass``) so this per-VI query surfaces
    them without a second class-file parse.
    """

    vi_name: str
    scope: str
    is_accessor: bool
    accessor_type: str | None
    accessor_field: str | None
    is_static: bool = False
    must_override: bool = False
    must_call_parent: bool = False


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

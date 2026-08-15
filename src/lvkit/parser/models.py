"""Shared dataclasses for parser module."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import ClusterField, EventFrame, LVType, Tunnel

if TYPE_CHECKING:
    from .layout import Layout


@dataclass
class ParsedType:
    """Type info extracted from XML - clean, no TypeID strings.

    This is the parser's output format for types. It contains everything
    that can be determined from the single VI's XML, without loading
    external files.

    The graph layer enriches this to LVType by adding:
    - values (enum members from vilib_resolver)
    """
    kind: str  # "primitive", "cluster", "array", "typedef_ref"
    type_name: str  # "Path", "Cluster", "NumInt32"
    typedef_path: str | None = None
    typedef_name: str | None = None  # Qualified: "sysdir.llb:Type.ctl"
    ref_type: str | None = None  # "UDClassInst", "Queue", etc.
    classname: str | None = None  # "Lib.lvlib:TestCase.lvclass"
    fields: list[ClusterField] | None = None  # Recursive cluster fields
    enum_values: dict | None = None  # {name: EnumValue} from VCTP
    element_type: ParsedType | None = None  # Array element type (recursive)
    dimensions: int | None = None  # Array dimension count


@dataclass
class ParsedNode:
    """A node in the block diagram (SubVI call, primitive, or terminal).

    This is the base class. Subclasses in node_types.py add type-specific fields.
    """
    uid: str
    node_type: str  # XML class: "iUse", "prim", "cpdArith", "aBuild", etc.
    name: str | None = None
    label: str | None = None  # LabVIEW label (partID 16), see extract_label
    caption: str | None = None  # LabVIEW caption (partID 82), see extract_caption


@dataclass
class ParsedConstant:
    """A constant value on the block diagram."""
    uid: str
    type_desc: str
    value: str
    label: str | None = None
    caption: str | None = None  # LabVIEW caption (partID 82), see extract_caption
    # Raw printf-style display-format string from the constant's own numeric
    # label part (e.g. '%.0x' = hex, '%.2f' = 2 decimal digits), verbatim
    # from the DCO's ddo/partsList/numLabel/format XML element. None when
    # the constant isn't numeric or carries no explicit format (LabVIEW
    # default decimal display). Interpreted at render time — the parser
    # does not decide what it means.
    display_format: str | None = None


@dataclass
class ParsedWire:
    """A wire connecting terminals."""
    uid: str
    from_term: str
    to_term: str


@dataclass
class ParsedFPTerminal:
    """A front panel terminal (VI input or output)."""
    uid: str
    fp_dco_uid: str  # Links to front panel control/indicator
    name: str | None = None
    is_indicator: bool = False  # True = output, False = input (control)
    parsed_type: ParsedType | None = None  # Type info from same VI's XML


@dataclass
class ParsedTerminalInfo:
    """Detailed info about a terminal for graph-native representation."""
    uid: str
    parent_uid: str
    index: int  # Position in parent's termList
    is_output: bool  # True if output terminal (data flows out)
    parsed_type: ParsedType | None = None  # Type info from same VI's XML
    name: str | None = None  # Terminal name (from FP, primitive ref, or SubVI)
    inverted: bool = False  # DCO objFlags bit 16: "Not" applied to this terminal


class ParsedWiringRule(IntEnum):
    """Terminal wiring rule - controls required/recommended/optional status.

    ``IntEnum`` so a member IS its LabVIEW wiring-rule int (0-4): it compares
    equal to the raw ``Terminal.wiring_rule`` value and serializes as that int,
    while giving the value set a name, iteration, and value->name lookup.
    """
    INVALID = 0
    REQUIRED = 1
    RECOMMENDED = 2
    OPTIONAL = 3
    DYNAMIC_DISPATCH = 4


# TunnelMapping moved to graph_types.Tunnel - import from there


@dataclass
class ParsedLoopStructure:
    """A loop structure (while or for) on the block diagram.

    Contains:
    - Loop boundary terminals that connect to tunnels
    - Tunnel mappings linking outer<->inner terminals
    - Reference to inner diagram containing loop body operations
    - Stop condition terminal (for while loops)
    - Stop condition polarity (for while loops)
    """
    uid: str
    loop_type: str  # "whileLoop" or "forLoop"
    boundary_terminal_uids: list[str] = field(default_factory=list)
    tunnels: list[Tunnel] = field(default_factory=list)
    inner_diagram_uid: str | None = None
    inner_node_uids: list[str] = field(default_factory=list)
    stop_condition_terminal_uid: str | None = None  # While loop stop (lTst)
    # While loop conditional-terminal polarity. True = Continue-if-True
    # (loop keeps running while the condition is True; stops when False).
    # False (default) = Stop-if-True (loop stops when the condition is
    # True). Derived from loopTestDCO's own <objFlags> bit 16. See
    # .tmp/task19_findings.md for the data evidence establishing this
    # polarity: bit 16 SET -> Stop-if-True, bit 16 CLEAR -> Continue-if-True
    # (the opposite of TERMINAL_DCO_INVERTED's meaning on cpdArith
    # terminals, where bit 16 set means "invert").
    stop_condition_inverted: bool = False
    # For-loop parallelism -- True when the forLoop element has a child
    # <ParForWorkers uid=.../>. Always False for while loops.
    parallel: bool = False
    # <ParForNumStaticWorkers>, hex-parsed (e.g. "08" -> 8); None when absent
    # or 0 (no static worker count configured).
    parallel_static_workers: int | None = None


from ..models import CaseFrame, SequenceFrame  # noqa: E402


@dataclass
class ParsedDecomposeRecomposeStructure:
    """An In Place Element Structure on the block diagram.

    Decomposes a cluster/array/DVR into fields at entry, executes inner
    operations that modify those fields, then recomposes at exit.
    In Python codegen, this is transparent (no control flow) — just field
    access and in-place write-back.
    """
    uid: str
    tunnels: list[Tunnel] = field(default_factory=list)
    inner_node_uids: list[str] = field(default_factory=list)


@dataclass
class SelectorTable:
    """A case structure's stored selector-value table from the dataspace DFDS.

    LabVIEW does not store per-frame selector labels in the block-diagram heap;
    it stores them once, in the default-data space (the main ``*.xml``), as a
    ``DataFill`` cluster of shape::

        {I32 displayed_frame, I32 range_count,
         Array[Cluster{I32 start, I32 end, U8, U8, I16 diagram_idx}],
         Array[String] value_strings,
         Cluster trailer}

    For a STRING selector ``start``/``end`` index ``value_strings`` (a frame can
    match several strings); otherwise they are literal numeric/enum values. A
    frame's index is ``diagram_idx``; the frame that appears in no range is the
    (implicit) Default. ``displayed_frame`` is the frame LabVIEW last showed.
    """

    type_id: int
    displayed_frame: int
    #: (start, end, diagram_idx) — inclusive value range → frame index
    ranges: list[tuple[int, int, int]] = field(default_factory=list)
    #: value strings for a string selector; empty for numeric/enum/boolean
    strings: list[str] = field(default_factory=list)

    @property
    def has_strings(self) -> bool:
        return bool(self.strings)


@dataclass
class ParsedCaseStructure:
    """A case structure on the block diagram."""

    uid: str
    selector_terminal_uid: str | None = None
    selector_type: str | None = None
    frames: list[CaseFrame] = field(default_factory=list)
    tunnels: list[Tunnel] = field(default_factory=list)
    # VCTP index of the caseSel selector-value type (``typeDesc TypeID(N)``).
    # Assigned in DCO-enumeration order, so it orders cases consistently with
    # the dataspace ``DataFill`` TypeID order — used to correlate each case to
    # its ``SelectorTable``.
    selector_vctp_index: int | None = None
    # Frame LabVIEW last displayed (from the correlated SelectorTable). None if
    # no table correlated. Consumed by the renderer's faithful initial view.
    displayed_frame: int | None = None
    # "Case Insensitive Match" enabled (string selectors). Default matching is
    # case-sensitive; LabVIEW 2015+ shows an "A=a" badge when this is set.
    case_insensitive: bool = False


@dataclass
class ParsedDisableStructure:
    """A Diagram/Conditional Disable structure on the block diagram.

    Serialized as ``class="commentNode"`` in the heap -- the SAME element
    class name suggested a plain free-text comment might share, but every
    ``commentNode`` observed in the corpus (152 instances / 112 real VIs)
    carries subdiagrams; a plain label is a separate element
    (``class="label"``). See ``parser/nodes/disable.py:is_disable_structure``,
    which still gates on the actual structural feature (a ``diagramList`` of
    >=1 ``diag`` children) rather than trusting that invariant blindly.

    Frame-bearing like a case (each ``diag`` child is a frame; exactly one is
    "active") but has NO selector terminal -- the active frame is fixed at
    compile/edit time (the heap's ``activeDiag`` index, driven by a
    conditional-compile symbol for a Conditional Disable Structure, or by the
    user's Enable/Disable toggle for a plain Diagram Disable Structure), not
    chosen by a runtime wire value.
    """

    uid: str
    frames: list[CaseFrame] = field(default_factory=list)
    tunnels: list[Tunnel] = field(default_factory=list)
    # Index into ``frames`` of the enabled/active subdiagram (heap
    # ``activeDiag``). None if the heap value didn't parse.
    active_frame: int | None = None


@dataclass
class ParsedFlatSequenceStructure:
    """A flat sequence structure on the block diagram."""

    uid: str
    tunnels: list[Tunnel] = field(default_factory=list)
    frames: list[SequenceFrame] = field(default_factory=list)


@dataclass
class ParsedEventStructure:
    """An Event Structure on the block diagram.

    Structurally close to a case structure — one frame ("diag") per
    registered event, border tunnels threading data through every frame —
    but the active frame is chosen at runtime by whichever event fires, not
    a selector wire. See parser/nodes/event.py.
    """

    uid: str
    frames: list[EventFrame] = field(default_factory=list)
    tunnels: list[Tunnel] = field(default_factory=list)
    # Frame LabVIEW last displayed (heap ``dIdx``), whose ``event_label``
    # came from the heap's own precomputed ``selString`` text — the
    # faithful initial view. None if ``dIdx`` was out of range.
    displayed_frame: int | None = None
    # uids of frames' Event FILTER Node (heap class ``eventDataNode``, same as
    # the Event Data Node — see module docstring), from the eventStruct's own
    # ``filterNodeList``. The renderer uses this to tell a frame's Filter Node
    # apart from its Data Node (same heap class, only this list says which is
    # which) so it draws the Filter Node's accent band on the opposite edge.
    filter_node_uids: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ParsedConnectorPaneSlot:
    """A slot on the connector pane."""
    index: int  # Slot position (0-based)
    fp_dco_uid: str | None = None  # UID of the connected fPDCO
    is_output: bool = False  # True if output terminal
    wiring_rule: ParsedWiringRule = ParsedWiringRule.INVALID
    type_id: str | None = None  # TypeID reference


@dataclass
class ParsedConnectorPane:
    """The VI's connector pane - defines its external interface."""
    pattern_id: int  # conId - identifies the connector pane pattern
    slots: list[ParsedConnectorPaneSlot] = field(default_factory=list)

    def get_connected_uids(self) -> list[str]:
        """Get UIDs of all controls/indicators connected to the pane."""
        return [s.fp_dco_uid for s in self.slots if s.fp_dco_uid]


@dataclass
class ParsedTypeDefRef:
    """A reference to a vilib TypeDef/custom control."""
    type_id: int
    name: str  # e.g., "System Directory Type.ctl"
    vilib_path: str  # e.g., "Utility/sysdir.llb"


@dataclass
class ParsedResolvedTypeDefValue:
    """A resolved typedef enum value with OS paths."""
    name: str
    description: str
    windows_path: str | None = None
    unix_path: str | None = None


@dataclass
class ParsedDefaultValue:
    """A default value from the DFDS section."""
    type_id: int
    values: list[Any]  # Parsed values (bool, int, float, str, etc.)
    structure: str  # "Cluster", "Array", "scalar", etc.


@dataclass
class ParsedDependencyRef:
    """A dependency recorded by LabVIEW in a LinkSavePathRef element.

    Spans every file type LabVIEW tracks: .vi, .lvclass, .ctl, .lvlib.
    """
    name: str  # Leaf filename, e.g., "TestCase.lvclass" or "listTestMethods.vi"
    path_tokens: list[str]  # Raw path tokens from LinkSavePathRef/String
    qualified_name: str | None = None  # e.g., "TestCase.lvclass:TestCase_Init.vi"

    def get_relative_path(self) -> str:
        """Get a display string for the path tokens (display only).

        For vilib/userlib deps, returns the path relative to the library root.
        For caller-relative deps, returns the raw token join (leading empty
        strings appear as leading slashes — not a valid file path).

        Use ``resolve_against()`` for actual path resolution.
        """
        if self.path_tokens and self.path_tokens[0] in ("<vilib>", "<userlib>"):
            return "/".join(self.path_tokens[1:])
        return "/".join(self.path_tokens)

    def resolve_against(
        self,
        caller_file: Path,
        vilib_root: Path | None = None,
        userlib_root: Path | None = None,
    ) -> Path | None:
        """Resolve LabVIEW's LinkSavePathRef tokens to an absolute path.

        Convention: start at the caller file itself, then each leading
        empty string pops one level (1 empty -> caller's containing
        directory, 2 empties -> its parent, etc.). Non-empty tokens are
        appended as path components. If the first token is <vilib> /
        <userlib>, the corresponding root is used as the base instead.
        """
        tokens = self.path_tokens
        if not tokens:
            return None

        if tokens[0] == "<vilib>":
            if vilib_root is None:
                return None
            base: Path = vilib_root
            rest = tokens[1:]
        elif tokens[0] == "<userlib>":
            if userlib_root is None:
                return None
            base = userlib_root
            rest = tokens[1:]
        else:
            # Each leading empty = one '..' starting from the caller file
            empties = 0
            for tok in tokens:
                if tok == "":
                    empties += 1
                else:
                    break
            base = caller_file
            for _ in range(empties):
                base = base.parent
            rest = tokens[empties:]

        if not rest:
            return None
        return (base / Path(*rest)).resolve()


# Backward compatibility alias — all new code uses ParsedDependencyRef
ParsedSubVIPathRef = ParsedDependencyRef


@dataclass
class ParsedFPDCOType:
    """Type info for a front panel DCO (data container object)."""
    uid: str
    type_desc: str  # e.g., "TypeID(1)"


@dataclass
class ParsedFPDCOTypeMap:
    """Collection of FP DCO types from an FP XML file."""
    types: list[ParsedFPDCOType] = field(default_factory=list)

    def get_type(self, dco_uid: str) -> str | None:
        """Get typeDesc for a DCO by UID."""
        return next(
            (t.type_desc for t in self.types if t.uid == dco_uid), None,
        )


@dataclass
class ParsedFPControl:
    """A control or indicator on the front panel."""
    uid: str
    name: str
    control_type: str  # stdString, stdNumeric, stdBool, stdPath, stdEnum, etc.
    bounds: tuple[int, int, int, int]  # top, left, bottom, right
    is_indicator: bool = False  # True if output, False if input
    type_desc: str | None = None
    default_value: str | None = None
    enum_values: list[str] = field(default_factory=list)
    ddo_uid: str | None = None  # UID of the inner ddo element (for ctlRefConst lookup)
    children: list[ParsedFPControl] = field(default_factory=list)  # For clusters


@dataclass
class ParsedFrontPanel:
    """Parsed front panel representation.

    Contains rich control details for UI generation.
    """
    controls: list[ParsedFPControl]
    panel_bounds: tuple[int, int, int, int]
    title: str | None = None


@dataclass
class ParsedVIMetadata:
    """VI-level metadata extracted from XML.

    Contains identity and reference information about the VI.
    Does NOT contain block diagram content.
    """
    qualified_name: str | None = None  # e.g., "Library.lvlib:VI.vi"
    # The VI's OWNERSHIP CHAIN from its own ``<LIBN>`` block (owning .lvlib /
    # .lvclass, outermost first) — e.g. ``["NI …SDK.lvlib", "Foo.lvclass"]``. The
    # VI is self-describing, so this is present even for an isolated ``.vi`` (no
    # class load, no filesystem). Used ONLY to build a DISPLAY-qualified name
    # (``chain:leaf``); ``qualified_name`` above stays the bare resolution key.
    owning_libraries: list[str] = field(default_factory=list)
    source_path: str | None = None  # Path to original .vi file
    type_map: dict[int, LVType] = field(default_factory=dict)  # TypeID → LVType mapping
    subvi_qualified_names: list[str] = field(default_factory=list)  # From VIVI entries
    iuse_to_qualified_name: dict[str, str] = field(
        default_factory=dict,
    )  # iUse UID → qualified name
    dependency_refs: list[ParsedDependencyRef] = field(
        default_factory=list,
    )  # Dependency path refs from LIvi LinkSavePathRef (all file types)


@dataclass
class ParsedBlockDiagram:
    """Parsed block diagram representation.

    Contains only block diagram content - metadata is in VIMetadata.
    """
    nodes: list[ParsedNode]
    constants: list[ParsedConstant]
    wires: list[ParsedWire]
    fp_terminals: list[ParsedFPTerminal] = field(default_factory=list)
    enum_labels: dict[str, list[str]] = field(default_factory=dict)
    terminal_info: dict[str, ParsedTerminalInfo] = field(default_factory=dict)
    loops: list[ParsedLoopStructure] = field(default_factory=list)
    case_structures: list[ParsedCaseStructure] = field(default_factory=list)
    flat_sequences: list[ParsedFlatSequenceStructure] = field(default_factory=list)
    decompose_structures: list[ParsedDecomposeRecomposeStructure] = field(
        default_factory=list,
    )
    disable_structures: list[ParsedDisableStructure] = field(default_factory=list)
    event_structures: list[ParsedEventStructure] = field(default_factory=list)
    # Maps sRN UID → containing structure UID (for scoped terminal collection)
    srn_to_structure: dict[str, str] = field(default_factory=dict)

    def get_node(self, uid: str) -> ParsedNode | None:
        """Get a node by UID."""
        for node in self.nodes:
            if node.uid == uid:
                return node
        return None

    def get_parent_uid(self, terminal_uid: str) -> str | None:
        """Get parent node UID for a terminal."""
        info = self.terminal_info.get(terminal_uid)
        return info.parent_uid if info else None

    def get_loop(self, uid: str) -> ParsedLoopStructure | None:
        """Get a loop by UID."""
        for loop in self.loops:
            if loop.uid == uid:
                return loop
        return None

    def get_tunnel_mapping(self, terminal_uid: str) -> Tunnel | None:
        """Find tunnel mapping for a terminal (either outer or inner)."""
        for loop in self.loops:
            for tunnel in loop.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        # Also check case structure tunnels
        for case_struct in self.case_structures:
            for tunnel in case_struct.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        # Also check flat sequence tunnels
        for flat_seq in self.flat_sequences:
            for tunnel in flat_seq.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        # Also check IPES tunnels
        for ds in self.decompose_structures:
            for tunnel in ds.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        # Also check event structure tunnels
        for es in self.event_structures:
            for tunnel in es.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        return None

    def get_case_structure(self, uid: str) -> ParsedCaseStructure | None:
        """Get a case structure by UID."""
        for case_struct in self.case_structures:
            if case_struct.uid == uid:
                return case_struct
        return None


@dataclass
class ParsedVI:
    """Complete parsed VI - everything needed for graph/codegen/docs.

    Single return type from parse_vi() containing all VI components.
    """
    metadata: ParsedVIMetadata
    block_diagram: ParsedBlockDiagram
    front_panel: ParsedFrontPanel
    connector_pane: ParsedConnectorPane | None = None
    # Block-diagram geometry (node/terminal/wire bounds), populated only when
    # parse_vi(..., layout=True) — the geometry half of the parse. None for
    # codegen, which needs no positions and pays nothing. See parser/layout.py.
    layout: Layout | None = None

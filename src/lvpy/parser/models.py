"""Shared dataclasses for parser module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..graph_types import ClusterField, LVType, Tunnel


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


@dataclass
class ParsedNode:
    """A node in the block diagram (SubVI call, primitive, or terminal).

    This is the base class. Subclasses in node_types.py add type-specific fields.
    """
    uid: str
    node_type: str  # XML class: "iUse", "prim", "cpdArith", "aBuild", etc.
    name: str | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)


@dataclass
class ParsedConstant:
    """A constant value on the block diagram."""
    uid: str
    type_desc: str
    value: str
    label: str | None = None


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


class ParsedWiringRule:
    """Terminal wiring rule - controls required/recommended/optional status."""
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
    """
    uid: str
    loop_type: str  # "whileLoop" or "forLoop"
    boundary_terminal_uids: list[str] = field(default_factory=list)
    tunnels: list[Tunnel] = field(default_factory=list)
    inner_diagram_uid: str | None = None
    inner_node_uids: list[str] = field(default_factory=list)
    stop_condition_terminal_uid: str | None = None  # While loop stop (lTst)


from ..graph_types import CaseFrame, SequenceFrame  # noqa: E402


@dataclass
class ParsedCaseStructure:
    """A case structure on the block diagram."""

    uid: str
    selector_terminal_uid: str | None = None
    selector_type: str | None = None
    frames: list[CaseFrame] = field(default_factory=list)
    tunnels: list[Tunnel] = field(default_factory=list)


@dataclass
class ParsedFlatSequenceStructure:
    """A flat sequence structure on the block diagram."""

    uid: str
    tunnels: list[Tunnel] = field(default_factory=list)
    frames: list[SequenceFrame] = field(default_factory=list)


@dataclass
class ParsedConnectorPaneSlot:
    """A slot on the connector pane."""
    index: int  # Slot position (0-based)
    fp_dco_uid: str | None = None  # UID of the connected fPDCO
    is_output: bool = False  # True if output terminal
    wiring_rule: int = 0  # ParsedWiringRule value (0-4)
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
class ParsedSubVIPathRef:
    """A SubVI reference with path hints from the XML."""
    name: str  # VI name, e.g., "Create Dir if Non-Existant__ogtk.vi"
    path_tokens: list[str]  # Path components
    is_vilib: bool = False  # True if from <vilib>
    is_userlib: bool = False  # True if from <userlib>
    qualified_name: str | None = None  # e.g., "Library.lvlib:VI.vi"

    def get_relative_path(self) -> str:
        """Get the relative path under vilib/userlib."""
        if self.path_tokens and self.path_tokens[0] in ("<vilib>", "<userlib>"):
            return "/".join(self.path_tokens[1:])
        return "/".join(self.path_tokens)


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
        for t in self.types:
            if t.uid == dco_uid:
                return t.type_desc
        return None


@dataclass
class ParsedFPControl:
    """A control or indicator on the front panel.

    Used for NiceGUI UI generation and rich control details.
    """
    uid: str
    name: str
    control_type: str  # stdString, stdNumeric, stdBool, stdPath, stdEnum, etc.
    bounds: tuple[int, int, int, int]  # top, left, bottom, right
    is_indicator: bool = False  # True if output, False if input
    type_desc: str | None = None
    default_value: str | None = None
    enum_values: list[str] = field(default_factory=list)
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
    source_path: str | None = None  # Path to original .vi file
    type_map: dict[int, LVType] = field(default_factory=dict)  # TypeID → LVType mapping
    subvi_qualified_names: list[str] = field(default_factory=list)  # From VIVI entries
    iuse_to_qualified_name: dict[str, str] = field(
        default_factory=dict,
    )  # iUse UID → qualified name
    subvi_path_refs: list[ParsedSubVIPathRef] = field(
        default_factory=list,
    )  # SubVI path hints


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
    # Maps sRN UID → containing structure UID (for scoped terminal collection)
    srn_to_structure: dict[str, str] = field(default_factory=dict)

    @property
    def term_to_parent(self) -> dict[str, str]:
        """Map terminal UID to parent node UID, built from terminal_info."""
        return {uid: info.parent_uid for uid, info in self.terminal_info.items()}

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

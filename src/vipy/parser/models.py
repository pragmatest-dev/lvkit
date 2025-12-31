"""Shared dataclasses for parser module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    """A node in the block diagram (SubVI call, primitive, or terminal)."""
    uid: str
    node_type: str  # "iUse" (SubVI), "prim" (primitive), "term" (terminal)
    name: str | None = None
    prim_index: int | None = None
    prim_res_id: int | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)


@dataclass
class Constant:
    """A constant value on the block diagram."""
    uid: str
    type_desc: str
    value: str
    label: str | None = None


@dataclass
class Wire:
    """A wire connecting terminals."""
    uid: str
    from_term: str
    to_term: str


@dataclass
class FPTerminal:
    """A front panel terminal (VI input or output)."""
    uid: str
    fp_dco_uid: str  # Links to front panel control/indicator
    name: str | None = None
    is_indicator: bool = False  # True = output, False = input (control)


@dataclass
class TerminalInfo:
    """Detailed info about a terminal for graph-native representation."""
    uid: str
    parent_uid: str
    index: int  # Position in parent's termList
    is_output: bool  # True if output terminal (data flows out)
    type_id: str | None = None  # e.g., "TypeID(5)" or resolved type name
    name: str | None = None  # Terminal name (from FP, primitive ref, or SubVI)


class WiringRule:
    """Terminal wiring rule - controls required/recommended/optional status."""
    INVALID = 0
    REQUIRED = 1
    RECOMMENDED = 2
    OPTIONAL = 3
    DYNAMIC_DISPATCH = 4


@dataclass
class TunnelMapping:
    """Maps outer loop terminal to inner terminal.

    In LabVIEW loops, data enters/exits via tunnels:
    - lSR (left shift register): Input tunnel, value persists across iterations
    - rSR (right shift register): Output tunnel, value persists across iterations
    - lpTun (loop tunnel): Simple pass-through, same value each iteration
    - lMax: Accumulator/max output
    """
    outer_terminal_uid: str  # Terminal on loop boundary (outside)
    inner_terminal_uid: str  # Terminal inside the loop diagram
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax"
    paired_terminal_uid: str | None = None  # For shift registers: the other side

    @property
    def direction(self) -> str:
        """Return 'in' or 'out' based on tunnel type."""
        if self.tunnel_type == "lSR":
            return "in"
        if self.tunnel_type in ("rSR", "lMax"):
            return "out"
        # lpTun can be either - caller must determine from context
        return "unknown"


@dataclass
class LoopStructure:
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
    tunnels: list[TunnelMapping] = field(default_factory=list)
    inner_diagram_uid: str | None = None
    inner_node_uids: list[str] = field(default_factory=list)
    stop_condition_terminal_uid: str | None = None  # While loop stop condition (lTst terminal)


@dataclass
class ConnectorPaneSlot:
    """A slot on the connector pane."""
    index: int  # Slot position (0-based)
    fp_dco_uid: str | None = None  # UID of the connected fPDCO
    is_output: bool = False  # True if output terminal
    wiring_rule: int = 0  # WiringRule value (0-4)
    type_id: str | None = None  # TypeID reference


@dataclass
class ConnectorPane:
    """The VI's connector pane - defines its external interface."""
    pattern_id: int  # conId - identifies the connector pane pattern
    slots: list[ConnectorPaneSlot] = field(default_factory=list)

    def get_connected_uids(self) -> list[str]:
        """Get UIDs of all controls/indicators connected to the pane."""
        return [s.fp_dco_uid for s in self.slots if s.fp_dco_uid]


@dataclass
class TypeDefRef:
    """A reference to a vilib TypeDef/custom control."""
    type_id: int
    name: str  # e.g., "System Directory Type.ctl"
    vilib_path: str  # e.g., "Utility/sysdir.llb"


@dataclass
class ResolvedTypeDefValue:
    """A resolved typedef enum value with OS paths."""
    name: str
    description: str
    windows_path: str | None = None
    unix_path: str | None = None


@dataclass
class DefaultValue:
    """A default value from the DFDS section."""
    type_id: int
    values: list[Any]  # Parsed values (bool, int, float, str, etc.)
    structure: str  # "Cluster", "Array", "scalar", etc.


@dataclass
class SubVIPathRef:
    """A SubVI reference with path hints from the XML."""
    name: str  # VI name, e.g., "Create Dir if Non-Existant__ogtk.vi"
    path_tokens: list[str]  # Path components
    is_vilib: bool = False  # True if from <vilib>
    is_userlib: bool = False  # True if from <userlib>

    def get_relative_path(self) -> str:
        """Get the relative path under vilib/userlib."""
        if self.path_tokens and self.path_tokens[0] in ("<vilib>", "<userlib>"):
            return "/".join(self.path_tokens[1:])
        return "/".join(self.path_tokens)


@dataclass
class BlockDiagram:
    """Parsed block diagram representation."""
    nodes: list[Node]
    constants: list[Constant]
    wires: list[Wire]
    fp_terminals: list[FPTerminal] = field(default_factory=list)
    enum_labels: dict[str, list[str]] = field(default_factory=dict)
    terminal_info: dict[str, TerminalInfo] = field(default_factory=dict)
    loops: list[LoopStructure] = field(default_factory=list)

    def get_node(self, uid: str) -> Node | None:
        """Get a node by UID."""
        for node in self.nodes:
            if node.uid == uid:
                return node
        return None

    def get_parent_uid(self, terminal_uid: str) -> str | None:
        """Get parent node UID for a terminal."""
        info = self.terminal_info.get(terminal_uid)
        return info.parent_uid if info else None

    def get_loop(self, uid: str) -> LoopStructure | None:
        """Get a loop by UID."""
        for loop in self.loops:
            if loop.uid == uid:
                return loop
        return None

    def get_tunnel_mapping(self, terminal_uid: str) -> TunnelMapping | None:
        """Find tunnel mapping for a terminal (either outer or inner)."""
        for loop in self.loops:
            for tunnel in loop.tunnels:
                if tunnel.outer_terminal_uid == terminal_uid:
                    return tunnel
                if tunnel.inner_terminal_uid == terminal_uid:
                    return tunnel
        return None

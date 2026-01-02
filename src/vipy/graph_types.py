"""Shared dataclasses for VI graph representation.

These dataclasses are the canonical types used across:
- memory_graph.py (creates/enriches instances)
- parser/ (uses Tunnel)
- codegen/ (consumes for code generation)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Terminal:
    """A terminal on an operation node."""

    id: str
    index: int
    direction: str  # "input" or "output"
    type: str = "Any"
    name: str | None = None
    typedef_path: str | None = None  # Filesystem path to .ctl
    typedef_name: str | None = None  # Qualified name (e.g., "sysdir.llb:Type.ctl")
    callee_param_name: str | None = None  # Name in SubVI's signature


@dataclass
class Tunnel:
    """A tunnel connecting loop outer/inner terminals.

    In LabVIEW loops, data enters/exits via tunnels:
    - lSR (left shift register): Input tunnel, value persists across iterations
    - rSR (right shift register): Output tunnel, paired with lSR
    - lpTun (loop tunnel): Simple pass-through
    - lMax: Accumulator/max output
    """

    outer_terminal_uid: str
    inner_terminal_uid: str
    tunnel_type: str  # "lSR", "rSR", "lpTun", "lMax"
    paired_terminal_uid: str | None = None

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
class Operation:
    """An operation node (SubVI, primitive, loop)."""

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


@dataclass
class Constant:
    """A constant value node."""

    id: str
    value: Any
    type: str
    raw_value: str | None = None
    label: str | None = None


@dataclass
class FPTerminalNode:
    """A front panel terminal (input/output)."""

    id: str
    kind: str  # "input" or "output"
    name: str | None
    is_indicator: bool
    is_public: bool
    slot_index: int | None = None
    wiring_rule: int = 0
    type_desc: str | None = None
    control_type: str | None = None
    default_value: Any = None
    enum_values: list = field(default_factory=list)
    type: str | None = None  # Resolved type
    type_info: Any = None  # TypeInfo object (added during enrichment)


@dataclass
class Wire:
    """A wire (edge) in the dataflow graph."""

    from_terminal_id: str
    to_terminal_id: str
    from_parent_id: str | None = None
    to_parent_id: str | None = None
    from_parent_name: str | None = None
    to_parent_name: str | None = None
    from_parent_labels: list[str] = field(default_factory=list)
    to_parent_labels: list[str] = field(default_factory=list)

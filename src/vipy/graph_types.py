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
    description: str | None = None  # VI description/help text


@dataclass
class Constant:
    """A constant value node."""

    id: str
    value: Any
    lv_type: LVType | None = None  # Full type info (parsed from XML)
    raw_value: str | None = None
    name: str | None = None  # Label text for the constant


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
    type: str | None = None  # Resolved type (underlying_type string)
    lv_type: LVType | None = None  # Full LVType structure (unified type system)


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


# Type definition dataclasses

@dataclass
class EnumValue:
    """A single value in an enum typedef."""
    value: int
    description: str | None = None


# Mapping from LabVIEW type names to Python type annotations
_LV_TO_PYTHON_TYPE: dict[str, str] = {
    "NumInt8": "int", "NumInt16": "int", "NumInt32": "int", "NumInt64": "int",
    "NumUInt8": "int", "NumUInt16": "int", "NumUInt32": "int", "NumUInt64": "int",
    "NumFloat32": "float", "NumFloat64": "float",
    "String": "str",
    "Boolean": "bool",
    "Path": "Path",
    "Variant": "Any",
    "Void": "None",
}


# Mapping from Front Panel control types to LVType (shared source of truth)
def control_type_to_lvtype(control_type: str) -> LVType | None:
    """Map a LabVIEW control type to LVType.

    Args:
        control_type: Control type string like "stdPath", "stdString", etc.

    Returns:
        LVType instance or None if not recognized
    """
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


@dataclass
class LVType:
    """LabVIEW type structure - unified representation for all types.

    ALL types are represented as LVType, including:
    - primitives (kind="primitive")
    - enums (kind="enum")
    - clusters (kind="cluster")
    - arrays (kind="array")
    - rings (kind="ring")
    - typedef references (kind="typedef_ref") - lazy resolution

    Examples:
        LVType(kind="primitive", underlying_type="NumInt32")
        LVType(kind="enum", underlying_type="UInt16", values={...})
        LVType(kind="cluster", underlying_type="Cluster", fields=[...])
        LVType(kind="array", underlying_type="Array", element_type=LVType(...))
        LVType(kind="typedef_ref", typedef_path="vi.lib/Utility/sysdir.llb/...")
    """
    kind: str  # "primitive", "enum", "cluster", "array", "ring", "typedef_ref"
    underlying_type: str | None = None  # Base LabVIEW type (None for typedef_ref)

    # Kind-specific fields (all optional, set based on kind)
    values: dict[str, EnumValue] | None = None  # enum/ring
    fields: list[ClusterField] | None = None  # cluster
    element_type: LVType | None = None  # array
    dimensions: int | None = None  # array
    typedef_path: str | None = None  # typedef_ref - path to resolve
    typedef_name: str | None = None  # Qualified name (e.g., "sysdir.llb:Type.ctl")
    description: str | None = None  # Documentation text from typedef

    def to_python(self) -> str:
        """Render as Python type annotation string."""
        if self.kind == "primitive":
            return _LV_TO_PYTHON_TYPE.get(self.underlying_type or "", "Any")
        elif self.kind == "array":
            if self.element_type:
                inner = self.element_type.to_python()
            else:
                inner = "Any"
            result = f"list[{inner}]"
            # Nested lists for multi-dimensional arrays
            dims = self.dimensions or 1
            for _ in range(dims - 1):
                result = f"list[{result}]"
            return result
        elif self.kind == "cluster":
            # Use typedef_name if available, otherwise generic dict
            if self.typedef_name:
                # Clean up typedef name for use as class name
                name = self.typedef_name.split(":")[-1].replace(".ctl", "")
                return name.replace(" ", "")
            return "dict[str, Any]"
        elif self.kind in ("enum", "ring"):
            if self.typedef_name:
                name = self.typedef_name.split(":")[-1].replace(".ctl", "")
                return name.replace(" ", "")
            return "int"
        elif self.kind == "typedef_ref":
            if self.typedef_name:
                name = self.typedef_name.split(":")[-1].replace(".ctl", "")
                return name.replace(" ", "")
            return "Any"
        return "Any"


@dataclass
class ClusterField:
    """A field in a cluster.

    The type field is always an LVType - supports full nesting
    (clusters in clusters, arrays of clusters, etc.)
    """
    name: str
    type: LVType

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
    lv_type: LVType | None = None
    var_name: str | None = None  # set during codegen
    nmux_role: str | None = None  # "agg" or "list"
    nmux_field_index: int | None = None  # class field index
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    default_value: ScalarValue = None

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


class FPTerminal(Terminal):
    """A connector pane terminal on a VINode."""

    kind: Literal["fp"] = "fp"
    wiring_rule: int = 0  # 0=unknown, 1=required, 2=recommended, 3=optional
    is_indicator: bool = False
    is_public: bool = True
    control_type: str | None = None
    default_value: ScalarValue = None
    enum_values: list[str] = []


class TunnelTerminal(Terminal):
    """An outer or inner tunnel terminal on a StructureNode."""

    kind: Literal["tunnel"] = "tunnel"
    tunnel_type: str = ""  # "lSR", "rSR", "lpTun", "lMax", "caseSel"
    boundary: str = ""  # "outer" or "inner"
    paired_id: str | None = None  # matching terminal on other side


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


class SequenceFrame(Frame):
    """A frame in a flat or stacked sequence — executes in order."""

    index: int = 0


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


class SequenceOperation(Operation):
    """Flat or stacked sequence."""

    frames: list[SequenceFrame] = []


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

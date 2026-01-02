"""Code generation context - tracks variable bindings during traversal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vipy.graph_types import Constant, FPTerminalNode, Wire


@dataclass
class CodeGenContext:
    """Context that flows through code generation traversal.

    Tracks:
    - Variable bindings (terminal_id → variable_name)
    - Data flow connections for resolving sources
    - Imports accumulated during generation
    - Optional lookup for callee VI contexts (for SubVI parameter names)
    """

    bindings: dict[str, str] = field(default_factory=dict)
    data_flow: list[Wire] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)

    # Flow map for quick lookup: dest_terminal → source info
    _flow_map: dict[str, dict] = field(default_factory=dict, repr=False)

    # Set of terminal IDs that have wires connected
    _wired_terminals: set[str] = field(default_factory=set, repr=False)

    # Optional: callable to look up callee VI contexts for parameter names
    # Signature: (vi_name: str) -> dict | None
    vi_context_lookup: Any = field(default=None, repr=False)

    def __post_init__(self):
        """Build flow map from data flow."""
        self._build_flow_map()
        self._build_wired_set()

    def _build_flow_map(self) -> None:
        """Build terminal→source mapping from data flow."""
        for wire in self.data_flow:
            dest_id = wire.to_terminal_id
            src_id = wire.from_terminal_id
            if dest_id and src_id:
                self._flow_map[dest_id] = {
                    "src_terminal": src_id,
                    "src_parent_id": wire.from_parent_id,
                    "src_parent_name": wire.from_parent_name,
                    "src_parent_labels": wire.from_parent_labels,
                }

    def _build_wired_set(self) -> None:
        """Build set of terminals that have wires connected."""
        for wire in self.data_flow:
            src_id = wire.from_terminal_id
            dest_id = wire.to_terminal_id
            if src_id:
                self._wired_terminals.add(src_id)
            if dest_id:
                self._wired_terminals.add(dest_id)

    def is_wired(self, terminal_id: str) -> bool:
        """Check if a terminal has any wire connected."""
        return terminal_id in self._wired_terminals

    def bind(self, terminal_id: str, var_name: str) -> None:
        """Register a variable for a terminal."""
        self.bindings[terminal_id] = var_name

    def resolve(self, terminal_id: str, visited: set[str] | None = None) -> str | None:
        """Get variable name for a terminal, following data flow back to source.

        Traces through tunnels and connections to find the original source variable.
        Returns None if terminal cannot be resolved.
        """
        if visited is None:
            visited = set()

        if terminal_id in visited:
            return None  # Cycle detection
        visited.add(terminal_id)

        # Direct binding?
        if terminal_id in self.bindings:
            return self.bindings[terminal_id]

        # Trace through data flow
        if terminal_id not in self._flow_map:
            return None

        flow = self._flow_map[terminal_id]
        src_terminal = flow["src_terminal"]

        # Source terminal has binding?
        if src_terminal in self.bindings:
            return self.bindings[src_terminal]

        # Source parent has binding? (for constants/inputs)
        src_parent_id = flow["src_parent_id"]
        if src_parent_id in self.bindings:
            return self.bindings[src_parent_id]

        # Recursively trace through connections
        return self.resolve(src_terminal, visited)

    def merge(self, bindings: dict[str, str]) -> None:
        """Merge new bindings into context."""
        self.bindings.update(bindings)

    def child(self) -> CodeGenContext:
        """Create a child context for nested scopes (e.g., loop interior).

        Child inherits bindings but has own copy that doesn't affect parent.
        """
        return CodeGenContext(
            bindings=dict(self.bindings),  # Copy
            data_flow=self.data_flow,  # Share (read-only)
            imports=self.imports,  # Share (accumulate)
            _flow_map=self._flow_map,  # Share (read-only)
            vi_context_lookup=self.vi_context_lookup,  # Share
        )

    def get_callee_param_name(self, vi_name: str, slot_index: int) -> str | None:
        """Look up parameter name from callee VI context.

        Handles polymorphic VIs by checking variants when wrapper has no inputs.

        Args:
            vi_name: Name of the callee VI
            slot_index: Terminal slot index to look up

        Returns:
            Parameter name or None if not found
        """
        if not self.vi_context_lookup:
            return None

        callee_ctx = self.vi_context_lookup(vi_name)
        if not callee_ctx:
            return None

        # Look in inputs for matching slot_index
        for inp in callee_ctx.get("inputs", []):
            slot_idx = inp.slot_index if hasattr(inp, 'slot_index') else inp.get("slot_index")
            if slot_idx == slot_index:
                name = inp.name if hasattr(inp, 'name') else inp.get("name")
                return name

        # If no inputs found, check if this is a polymorphic wrapper
        # and look for variant VIs (named "Base - Variant.vi")
        if not callee_ctx.get("inputs"):
            base_name = vi_name.replace(".vi", "").replace(".VI", "")
            # Try common variant patterns
            for variant_suffix in [" - Traditional", " - Arrays"]:
                variant_vi = f"{base_name}{variant_suffix}.vi"
                # Handle __ogtk suffix
                if "__ogtk" in base_name:
                    parts = base_name.split("__")
                    variant_vi = f"{parts[0]}{variant_suffix}__{parts[1]}.vi"

                variant_ctx = self.vi_context_lookup(variant_vi)
                if variant_ctx and variant_ctx.get("inputs"):
                    for inp in variant_ctx.get("inputs", []):
                        slot_idx = inp.slot_index if hasattr(inp, 'slot_index') else inp.get("slot_index")
                        if slot_idx == slot_index:
                            name = inp.name if hasattr(inp, 'name') else inp.get("name")
                            return name

        return None

    def get_callee_output_name(self, vi_name: str, slot_index: int) -> str | None:
        """Look up output name from callee VI context.

        Handles polymorphic VIs by checking variants when wrapper has no outputs.

        Args:
            vi_name: Name of the callee VI
            slot_index: Terminal slot index to look up

        Returns:
            Output field name or None if not found
        """
        if not self.vi_context_lookup:
            return None

        callee_ctx = self.vi_context_lookup(vi_name)
        if not callee_ctx:
            return None

        # Look in outputs for matching slot_index
        for out in callee_ctx.get("outputs", []):
            slot_idx = out.slot_index if hasattr(out, 'slot_index') else out.get("slot_index")
            if slot_idx == slot_index:
                name = out.name if hasattr(out, 'name') else out.get("name")
                return name

        # If no outputs found, check if this is a polymorphic wrapper
        if not callee_ctx.get("outputs"):
            base_name = vi_name.replace(".vi", "").replace(".VI", "")
            for variant_suffix in [" - Traditional", " - Arrays"]:
                variant_vi = f"{base_name}{variant_suffix}.vi"
                if "__ogtk" in base_name:
                    parts = base_name.split("__")
                    variant_vi = f"{parts[0]}{variant_suffix}__{parts[1]}.vi"

                variant_ctx = self.vi_context_lookup(variant_vi)
                if variant_ctx and variant_ctx.get("outputs"):
                    for out in variant_ctx.get("outputs", []):
                        slot_idx = out.slot_index if hasattr(out, 'slot_index') else out.get("slot_index")
                        if slot_idx == slot_index:
                            name = out.name if hasattr(out, 'name') else out.get("name")
                            return name

        return None

    def add_import(self, import_stmt: str) -> None:
        """Add an import statement."""
        self.imports.add(import_stmt)

    @classmethod
    def from_vi_context(cls, vi_context: dict[str, Any]) -> CodeGenContext:
        """Create context from VI context dict.

        Initializes bindings for inputs and constants.
        """
        ctx = cls(data_flow=vi_context.get("data_flow", []))

        # Bind inputs (list of FPTerminalNode)
        for inp in vi_context.get("inputs", []):
            inp_id = inp.id
            inp_name = inp.name or "input"
            if inp_id:
                var_name = _to_var_name(inp_name)
                ctx.bind(inp_id, var_name)

        # Bind constants with proper formatting (list of Constant)
        for const in vi_context.get("constants", []):
            const_id = const.id
            if const_id:
                formatted = _format_constant(const)
                ctx.bind(const_id, formatted)

        return ctx


def _format_constant(const: Constant) -> str:
    """Format a constant value as a Python expression.

    Handles:
    - python_hint if available
    - Path types
    - Numeric strings
    - General values
    """
    # Prefer python_hint if available (stored in raw_value for now)
    python_hint = getattr(const, "python", None)
    if python_hint:
        return str(python_hint)

    value = const.value
    const_type = const.type or ""

    if value is None:
        return "None"

    # Handle Path types
    if const_type.lower() in ("path", "filepath"):
        return f"Path('{value}')"

    # Handle numeric values
    if isinstance(value, (int, float)):
        return str(value)

    # Handle string values that look like numbers
    if isinstance(value, str):
        # Handle LabVIEW empty string (represented as "" in XML)
        if value == '""':
            return "''"  # Empty string in Python

        # Strip surrounding quotes if present (LabVIEW string encoding)
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]

        # Try to parse as int
        try:
            int_val = int(value)
            return str(int_val)
        except ValueError:
            pass

        # Try to parse as float
        try:
            float_val = float(value)
            return str(float_val)
        except ValueError:
            pass

        # Check if it's a path-like string
        if "/" in value or "\\" in value or value.endswith((".vi", ".ini", ".txt")):
            return f"Path('{value}')"

        # Regular string
        return repr(value)

    # Default: repr
    return repr(value)


def _to_var_name(name: str) -> str:
    """Convert a name to a valid Python variable name."""
    if not name:
        return "var"
    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    if result and not result[0].isalpha() and result[0] != "_":
        result = "var_" + result
    if not result:
        result = "var"
    return result

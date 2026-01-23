"""Code generation context - tracks variable bindings during traversal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vipy.graph_types import Constant, Wire

from .ast_utils import to_var_name


@dataclass
class CodeGenContext:
    """Context that flows through code generation traversal.

    Tracks:
    - Variable bindings (terminal_id → variable_name)
    - Data flow connections for resolving sources
    - Imports accumulated during generation
    - Optional lookup for callee VI contexts (for SubVI parameter names)
    - VI name being generated (for terminal observation tracking)
    - Error handling mode (held error model for parallel branches)
    """

    bindings: dict[str, str] = field(default_factory=dict)
    data_flow: list[Wire] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)
    vi_name: str | None = None  # Name of VI being generated
    loop_depth: int = 0  # Nesting depth for index variable naming (i, j, k, ...)
    use_held_error_model: bool = False  # Enable held error model for parallel branches

    # Flow map for quick lookup: dest_terminal → source info
    _flow_map: dict[str, dict] = field(default_factory=dict, repr=False)

    # Reverse flow map: src_terminal → list of dest info (for downstream name lookup)
    _reverse_flow_map: dict[str, list[dict]] = field(default_factory=dict, repr=False)

    # Set of terminal IDs that have wires connected
    _wired_terminals: set[str] = field(default_factory=set, repr=False)

    # Optional: callable to look up callee VI contexts for parameter names
    # Signature: (vi_name: str) -> dict | None
    vi_context_lookup: Any = field(default=None, repr=False)

    # Optional: callable to resolve import paths for SubVI dependencies
    # Signature: (subvi_name: str) -> str (e.g., "from ..module import func")
    import_resolver: Any = field(default=None, repr=False)

    def __post_init__(self):
        """Build flow maps from data flow."""
        self._build_flow_map()
        self._build_wired_set()

    def _build_flow_map(self) -> None:
        """Build terminal→source and source→dest mappings from data flow."""
        for wire in self.data_flow:
            dest_id = wire.to_terminal_id
            src_id = wire.from_terminal_id
            if dest_id and src_id:
                # Forward map: dest -> source
                self._flow_map[dest_id] = {
                    "src_terminal": src_id,
                    "src_parent_id": wire.from_parent_id,
                    "src_parent_name": wire.from_parent_name,
                    "src_parent_labels": wire.from_parent_labels,
                    "src_slot_index": wire.from_slot_index,
                }
                # Reverse map: source -> list of dests
                if src_id not in self._reverse_flow_map:
                    self._reverse_flow_map[src_id] = []
                self._reverse_flow_map[src_id].append({
                    "dest_terminal": dest_id,
                    "dest_parent_id": wire.to_parent_id,
                    "dest_parent_name": wire.to_parent_name,
                    "dest_parent_labels": wire.to_parent_labels,
                    "dest_slot_index": wire.to_slot_index,
                })

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

    def child(self, increment_loop_depth: bool = False) -> CodeGenContext:
        """Create a child context for nested scopes (e.g., loop interior).

        Child inherits bindings but has own copy that doesn't affect parent.

        Args:
            increment_loop_depth: If True, increment loop depth for nested loops
        """
        return CodeGenContext(
            bindings=dict(self.bindings),  # Copy
            data_flow=self.data_flow,  # Share (read-only)
            imports=self.imports,  # Share (accumulate)
            vi_name=self.vi_name,  # Share (for observation tracking)
            loop_depth=self.loop_depth + (1 if increment_loop_depth else 0),
            use_held_error_model=self.use_held_error_model,  # Inherit error model
            _flow_map=self._flow_map,  # Share (read-only)
            _reverse_flow_map=self._reverse_flow_map,  # Share (read-only)
            _wired_terminals=self._wired_terminals,  # Share (read-only)
            vi_context_lookup=self.vi_context_lookup,  # Share
            import_resolver=self.import_resolver,  # Share
        )

    def get_loop_index_var(self) -> str:
        """Get index variable name for current loop depth.

        Returns i, j, k, l, m, n for depths 0-5, then idx_6, idx_7, etc.
        """
        if self.loop_depth < 6:
            return "ijklmn"[self.loop_depth]
        return f"idx_{self.loop_depth}"

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
            if inp.slot_index == slot_index:
                return inp.name

        # If no inputs found, check if this is a polymorphic wrapper
        # Use explicit poly_variants from VI metadata
        poly_variants = callee_ctx.get("poly_variants", [])
        for variant_vi in poly_variants:
            variant_ctx = self.vi_context_lookup(variant_vi)
            if variant_ctx:
                for inp in variant_ctx.get("inputs", []):
                    if inp.slot_index == slot_index:
                        return inp.name

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
            if out.slot_index == slot_index:
                return out.name

        # If no outputs found, check if this is a polymorphic wrapper
        # Use explicit poly_variants from VI metadata
        poly_variants = callee_ctx.get("poly_variants", [])
        for variant_vi in poly_variants:
            variant_ctx = self.vi_context_lookup(variant_vi)
            if variant_ctx:
                for out in variant_ctx.get("outputs", []):
                    if out.slot_index == slot_index:
                        return out.name

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
                var_name = to_var_name(inp_name)
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
    - Enum values (pre-resolved in graph via lv_type)
    - python_hint if available
    - Path types
    - Numeric strings
    - General values
    """
    # If enum was resolved in graph, use it
    if const.lv_type and const.lv_type.kind == "enum" and const.lv_type.values:
        # Find which enum member matches this value
        try:
            int_value = int(const.value)
            for member_name, enum_val in const.lv_type.values.items():
                if enum_val.value == int_value:
                    # Derive Python class name from typedef_name
                    if const.lv_type.typedef_name:
                        from vipy.vilib_resolver import derive_python_name
                        class_name = derive_python_name(const.lv_type.typedef_name)
                        return f"{class_name}.{member_name}"
                    return str(const.value)
        except (ValueError, TypeError):
            pass

    # Prefer python_hint if available (stored in raw_value for now)
    python_hint = getattr(const, "python", None)
    if python_hint:
        return str(python_hint)

    value = const.value
    # Check if lv_type indicates this is a Path
    is_path = const.lv_type and const.lv_type.underlying_type == "Path"

    if value is None:
        return "None"

    # Handle Path types
    if is_path:
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

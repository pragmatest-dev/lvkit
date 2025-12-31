"""Code generation context - tracks variable bindings during traversal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CodeGenContext:
    """Context that flows through code generation traversal.

    Tracks:
    - Variable bindings (terminal_id → variable_name)
    - Data flow connections for resolving sources
    - Imports accumulated during generation
    """

    bindings: dict[str, str] = field(default_factory=dict)
    data_flow: list[dict[str, Any]] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)

    # Flow map for quick lookup: dest_terminal → source info
    _flow_map: dict[str, dict] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Build flow map from data flow."""
        self._build_flow_map()

    def _build_flow_map(self) -> None:
        """Build terminal→source mapping from data flow."""
        for flow in self.data_flow:
            dest_id = flow.get("to_terminal_id")
            src_id = flow.get("from_terminal_id")
            if dest_id and src_id:
                self._flow_map[dest_id] = {
                    "src_terminal": src_id,
                    "src_parent_id": flow.get("from_parent_id"),
                    "src_parent_name": flow.get("from_parent_name"),
                    "src_parent_labels": flow.get("from_parent_labels", []),
                }

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
        )

    def add_import(self, import_stmt: str) -> None:
        """Add an import statement."""
        self.imports.add(import_stmt)

    @classmethod
    def from_vi_context(cls, vi_context: dict[str, Any]) -> CodeGenContext:
        """Create context from VI context dict.

        Initializes bindings for inputs and constants.
        """
        ctx = cls(data_flow=vi_context.get("data_flow", []))

        # Bind inputs
        for inp in vi_context.get("inputs", []):
            inp_id = inp.get("id")
            inp_name = inp.get("name", "input")
            if inp_id:
                var_name = _to_var_name(inp_name)
                ctx.bind(inp_id, var_name)

        # Bind constants with proper formatting
        for const in vi_context.get("constants", []):
            const_id = const.get("id")
            if const_id:
                formatted = _format_constant(const)
                ctx.bind(const_id, formatted)

        return ctx


def _format_constant(const: dict[str, Any]) -> str:
    """Format a constant value as a Python expression.

    Handles:
    - python_hint if available
    - Path types
    - Numeric strings
    - General values
    """
    # Prefer python_hint if available
    python_hint = const.get("python")
    if python_hint:
        return str(python_hint)

    value = const.get("value")
    const_type = const.get("type", "")

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

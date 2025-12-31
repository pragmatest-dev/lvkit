"""Data flow tracing from VI graph.

Builds mappings from terminals to source variables by tracing wires.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TerminalInfo:
    """Information about a terminal."""
    id: str
    index: int
    direction: str  # "input" or "output"
    parent_id: str
    parent_type: str  # "operation", "constant", "input", "output"
    name: str | None = None
    type_hint: str | None = None


@dataclass
class WireInfo:
    """Information about a wire connection."""
    from_terminal: str
    to_terminal: str
    from_parent_id: str
    to_parent_id: str


class DataFlowTracer:
    """Traces data flow through VI graph to resolve variable sources.

    Given a VI context, builds mappings that allow answering:
    - What variable feeds into this terminal?
    - What terminals are wired?
    - What is the source of this output?
    """

    def __init__(self, vi_context: dict):
        """Initialize tracer with VI context.

        Args:
            vi_context: Context from graph.get_vi_context()
        """
        self._context = vi_context
        self._terminals: dict[str, TerminalInfo] = {}
        self._wires: list[WireInfo] = []
        self._wired_terminals: set[str] = set()
        self._flow_map: dict[str, WireInfo] = {}  # dest_terminal -> wire
        self._terminal_to_var: dict[str, str] = {}  # terminal_id -> variable name

        self._build_terminal_index()
        self._build_flow_map()

    def _build_terminal_index(self) -> None:
        """Index all terminals by ID."""
        # Index terminals from all sources
        for term in self._context.get("terminals", []):
            self._terminals[term["id"]] = TerminalInfo(
                id=term["id"],
                index=term.get("index", 0),
                direction=term.get("direction", "unknown"),
                parent_id=term.get("parent_id", ""),
                parent_type="unknown",
                name=term.get("name"),
                type_hint=term.get("type"),
            )

        # Also capture terminals embedded in operations
        for op in self._context.get("operations", []):
            for term in op.get("terminals", []):
                if term["id"] not in self._terminals:
                    labels = op.get("labels", [])
                    parent_type = "primitive" if "Primitive" in labels else "subvi" if "SubVI" in labels else "operation"
                    self._terminals[term["id"]] = TerminalInfo(
                        id=term["id"],
                        index=term.get("index", 0),
                        direction=term.get("direction", "unknown"),
                        parent_id=op.get("id", ""),
                        parent_type=parent_type,
                        name=term.get("name"),
                        type_hint=term.get("type"),
                    )

    def _build_flow_map(self) -> None:
        """Build mapping from destination terminals to their sources."""
        for flow in self._context.get("data_flow", []):
            from_term = flow.get("from_terminal_id")
            to_term = flow.get("to_terminal_id")

            if from_term and to_term:
                wire = WireInfo(
                    from_terminal=from_term,
                    to_terminal=to_term,
                    from_parent_id=flow.get("from_parent_id", ""),
                    to_parent_id=flow.get("to_parent_id", ""),
                )
                self._wires.append(wire)
                self._flow_map[to_term] = wire
                self._wired_terminals.add(from_term)
                self._wired_terminals.add(to_term)

    def is_wired(self, terminal_id: str) -> bool:
        """Check if a terminal has any wire connected."""
        return terminal_id in self._wired_terminals

    def get_source_terminal(self, terminal_id: str) -> str | None:
        """Get the source terminal that feeds into this terminal."""
        wire = self._flow_map.get(terminal_id)
        return wire.from_terminal if wire else None

    def get_terminal(self, terminal_id: str) -> TerminalInfo | None:
        """Get terminal info by ID."""
        return self._terminals.get(terminal_id)

    def register_variable(self, terminal_id: str, var_name: str) -> None:
        """Register that a terminal produces/holds a variable.

        Args:
            terminal_id: The terminal that produces this value
            var_name: The Python variable name
        """
        self._terminal_to_var[terminal_id] = var_name

    def get_variable(self, terminal_id: str) -> str | None:
        """Get the variable name for a terminal, if registered."""
        return self._terminal_to_var.get(terminal_id)

    def resolve_source(self, terminal_id: str) -> str | None:
        """Resolve what variable feeds into this terminal.

        Traces back through wires to find the source variable.

        Args:
            terminal_id: The destination terminal

        Returns:
            Variable name or None if can't resolve
        """
        # Check if we have a direct wire to this terminal
        wire = self._flow_map.get(terminal_id)
        if not wire:
            return None

        # Check if source terminal has a registered variable
        source_var = self._terminal_to_var.get(wire.from_terminal)
        if source_var:
            return source_var

        # Could recurse, but for now return None
        return None

    def get_wired_inputs(self, operation_id: str) -> list[tuple[int, str, str | None]]:
        """Get wired input terminals for an operation.

        Args:
            operation_id: The operation ID

        Returns:
            List of (index, terminal_id, source_var) sorted by index
        """
        results = []
        for term_id, term in self._terminals.items():
            if term.parent_id == operation_id and term.direction == "input":
                if self.is_wired(term_id):
                    source_var = self.resolve_source(term_id)
                    results.append((term.index, term_id, source_var))

        return sorted(results, key=lambda x: x[0])

    def get_wired_outputs(self, operation_id: str) -> list[tuple[int, str]]:
        """Get wired output terminals for an operation.

        Args:
            operation_id: The operation ID

        Returns:
            List of (index, terminal_id) sorted by index
        """
        results = []
        for term_id, term in self._terminals.items():
            if term.parent_id == operation_id and term.direction == "output":
                if self.is_wired(term_id):
                    results.append((term.index, term_id))

        return sorted(results, key=lambda x: x[0])

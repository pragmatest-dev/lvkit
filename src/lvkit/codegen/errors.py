"""Errors raised when code generation cannot write a node or value."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode
    from lvkit.models import UnwritableValue


class CodeGenError(Exception):
    """An invariant of code generation failed: a bug in lvkit, not something in
    the VI. Generation stops. What the VI contains and cannot be converted is a
    ``ConversionGap``."""

    def __init__(self, message: str, node: AnyGraphNode | None = None):
        self.node = node
        self.node_id = node.id if node else None
        self.node_name = node.name if node else None
        super().__init__(message)


class UnknownNodeError(ConversionGap):
    """A node type code generation has no generator for."""


def unwritable_value_error(
    what: str, name: str | None, value: UnwritableValue
) -> ConversionGap:
    """The gap for a ``what`` ("constant" or "control") named ``name`` that holds
    a part (``value``) code generation has no value to write for."""
    return ConversionGap(
        f"The {what} {name!r} holds {value.reason()}; code generation has no "
        "value to write for it."
    )


class MissingVIError(ConversionGap):
    """A call names a VI that was not found, so there is no module to import and
    no connector pane to name its terminals from. The call is the gap."""

    def __init__(self, name: str, recorded_path: str | None):
        self.name = name
        self.recorded_path = recorded_path
        # The wired terminals of the call (direction, index, type), added where the
        # gap is caught: whoever writes the VI later sees the pane it was called by.
        self.call_terminals: list[str] = []
        super().__init__(self.summary())

    def summary(self) -> str:
        where = f" ({self.recorded_path})" if self.recorded_path else ""
        return (
            f"The VI '{self.name}'{where} was not found; add it to the project "
            "or to a --search-path and generate again"
        )

    def __str__(self) -> str:
        lines = [self.summary()]
        if self.call_terminals:
            lines.append("  Terminals the call wires:")
            lines.extend(f"    - {line}" for line in self.call_terminals)
        return "\n".join(lines)

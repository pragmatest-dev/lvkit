"""Errors raised when code generation cannot write a node or value."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode
    from lvkit.models import UnwritableValue


class CodeGenError(Exception):
    """Raised when code generation fails for a node."""

    def __init__(self, message: str, node: AnyGraphNode | None = None):
        self.node = node
        self.node_id = node.id if node else None
        self.node_name = node.name if node else None
        super().__init__(message)


class UnknownNodeError(CodeGenError):
    """Raised when encountering an unsupported node type."""

    pass


class MissingDependencyError(CodeGenError):
    """Raised when a required dependency (SubVI, primitive) is missing."""

    pass


def unwritable_value_error(
    what: str, name: str | None, value: UnwritableValue
) -> CodeGenError:
    """The error for a ``what`` ("constant" or "control") named ``name`` that holds
    a part (``value``) code generation has no value to write for."""
    return CodeGenError(
        f"The {what} {name!r} holds {value.reason()}; code generation has no "
        "value to write for it."
    )

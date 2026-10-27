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

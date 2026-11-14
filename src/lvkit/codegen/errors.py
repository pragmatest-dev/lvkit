"""Errors raised when code generation cannot write a node or value."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.primitive_resolver import TerminalResolutionNeeded

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode
    from lvkit.models import UnwritableValue
    from lvkit.terminal_default import DefaultBehavior


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
    what: str, name: str | None, value: UnwritableValue, uid: str | None = None
) -> ConversionGap:
    """The gap for a ``what`` ("constant" or "control") named ``name`` (or, with no
    name, identified by its ``uid``) that holds a part (``value``) code
    generation has no value to write for."""
    if name:
        subject = f"The {what} {name!r}"
    elif uid:
        subject = f"The {what} (uid {uid})"
    else:
        subject = f"A {what}"
    return ConversionGap(
        f"{subject} holds {value.reason()}; code generation has no value to write "
        "for it."
    )


class MissingVIError(ConversionGap):
    """A call names a VI that was not found, so there is no module to import and
    no connector pane to name its terminals from. The call is the gap."""

    def __init__(
        self, name: str, recorded_path: str | None, reason: str | None = None
    ):
        self.name = name
        self.recorded_path = recorded_path
        self.reason = reason
        # The wired terminals of the call (direction, index, type), added where the
        # gap is caught: whoever writes the VI later sees the pane it was called by.
        self.call_terminals: list[str] = []
        super().__init__(self.summary())

    def summary(self) -> str:
        where = f" ({self.recorded_path})" if self.recorded_path else ""
        if self.reason:
            return f"The VI '{self.name}'{where} was not found: {self.reason}"
        return (
            f"The VI '{self.name}'{where} was not found at the path its caller "
            "records; put it there (a <userlib>/<vilib>/<instrlib> path needs "
            "that folder named with --userlib/--vilib/--instrlib) and generate "
            "again"
        )

    def __str__(self) -> str:
        lines = [self.summary()]
        if self.call_terminals:
            lines.append("  Terminals the call wires:")
            lines.extend(f"    - {line}" for line in self.call_terminals)
        return "\n".join(lines)


class UnknownDefault(ConversionGap):
    """An input the function's code reads is not wired, and the catalog does not
    know what the input holds then: it has neither a documented value nor a
    documented behaviour this generator writes."""

    def __init__(
        self,
        function: str,
        terminal: str,
        node: AnyGraphNode | None = None,
        behavior: DefaultBehavior | None = None,
    ):
        self.function = function
        self.terminal = terminal
        if behavior is None:
            known = "its default is not known"
        else:
            known = f"its default ({behavior.value}) is not written for this function"
        super().__init__(
            f"The input '{terminal}' of {function} is not wired and {known}", node
        )


class TerminalNotOnCall(TerminalResolutionNeeded):
    """The function's code reads an input the call does not have: the catalog
    numbers the function's terminals differently from the call."""

    def __init__(
        self,
        prim_id: str | int,
        function: str,
        terminal: str,
        terminal_type: str | None,
        node: AnyGraphNode | None = None,
    ):
        self.terminal = terminal
        super().__init__(prim_id, function, "input", terminal_type, [])
        self.node = node
        self.node_id = node.id if node else None
        self.node_name = node.name if node else None

    def summary(self) -> str:
        return (
            f"The input '{self.terminal}' of {self.prim_name} is not on this call: "
            "the catalog numbers the terminals of this function differently from "
            "the call"
        )

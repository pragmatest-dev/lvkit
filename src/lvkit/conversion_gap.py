"""The base of every error that means "this part of the VI cannot be converted".

A gap is something in the VI that code generation has no Python for: an unknown
primitive, a property with no Python meaning, a library file that is not there.
Generation does not stop for a gap. It records a warning and writes a
``NotImplementedError`` where the node would run (see ``codegen/gaps.py``).
Errors that mean the package cannot be written, or that lvkit itself is wrong,
are not gaps and do not derive from this class.

This module imports nothing at run time, so the resolver packages, the graph and
the code generator can all derive from it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode


class ConversionGap(Exception):
    """Something in the VI that code generation cannot convert."""

    def __init__(self, message: str, node: AnyGraphNode | None = None):
        self.node = node
        self.node_id = node.id if node else None
        self.node_name = node.name if node else None
        super().__init__(message)

    def summary(self) -> str:
        """The one-line reason shown in the warning and written into the
        generated ``NotImplementedError``."""
        return " ".join(str(self).split())

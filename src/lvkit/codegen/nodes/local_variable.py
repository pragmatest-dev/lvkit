"""A local variable or control reference node that names no front-panel control.

Reading one has no Python value ("This Application" is a LabVIEW server object);
the reference is a gap, its outputs ``None`` where consumers read them."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import LocalVariableNode

from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: LocalVariableNode, ctx: CodeGenContext) -> CodeFragment:
    raise ConversionGap(
        f"The reference {node.display_name!r} names no front-panel control, so "
        "there is no value to read",
        node,
    )

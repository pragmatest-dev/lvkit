"""A local variable or control reference node that names no front-panel control.

"This VI" is the VI being generated, and a VI is its function: the reference is
that function's name in the module, so a subVI that is passed it receives the
function and ``ref.__name__`` / ``inspect.getfile(ref)`` read its properties.
Any other such reference ("This Application") is a LabVIEW server object with no
Python value to read: a gap, its outputs ``None`` where consumers read them."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import LocalVariableNode
from lvkit.graph.vi_reference import is_this_vi

from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: LocalVariableNode, ctx: CodeGenContext) -> CodeFragment:
    if is_this_vi(node):
        return CodeFragment(bindings={node.terminals[0].id: ctx.function_name})
    raise ConversionGap(
        f"The reference {node.display_name!r} names no front-panel control, so "
        "there is no value to read",
        node,
    )

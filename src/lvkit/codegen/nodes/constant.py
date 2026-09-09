"""Code generator for constants."""

from __future__ import annotations

from lvkit.graph.models import ConstantNode

from ..context import CodeGenContext, _format_constant
from ..fragment import CodeFragment


def generate(node: ConstantNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a constant node.

    Top-level constants are already bound in context (this early-returns). A
    constant that ISN'T pre-bound — e.g. one nested inside a structure — is
    bound here to its literal VALUE via the same formatter, so a constant is
    always its value and never an undefined value-derived name.
    """
    const_id = node.id
    if not const_id:
        return CodeFragment.empty()

    if ctx.resolve(const_id) is not None:
        return CodeFragment.empty()

    return CodeFragment(bindings={const_id: _format_constant(node)})

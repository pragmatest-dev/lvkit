"""Code generator for constants."""

from __future__ import annotations

from lvkit.models import Operation

from ..ast_utils import to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: Operation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a constant node.

    Usually constants are already bound in context, so this may produce
    no statements. For labeled constants, it produces an assignment.
    """
    const_id = node.id
    if not const_id:
        return CodeFragment.empty()

    if ctx.resolve(const_id) is not None:
        return CodeFragment.empty()

    label = node.name
    if label:
        var_name = to_var_name(label)
        return CodeFragment(
            statements=[],
            bindings={const_id: var_name},
        )

    return CodeFragment.empty()

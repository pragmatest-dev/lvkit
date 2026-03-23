"""Code generator for constants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class ConstantCodeGen(NodeCodeGen):
    """Generate code for constant values.

    Constants are typically pre-bound in context with their literal value.
    This generator handles any that need explicit assignment.
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a constant node.

        Usually constants are already bound in context, so this may produce
        no statements. For labeled constants, it produces an assignment.
        """
        const_id = node.id
        if not const_id:
            return CodeFragment.empty()

        # Check if already has var_name
        if ctx.resolve(const_id) is not None:
            return CodeFragment.empty()

        # Get label (constants don't have value/label on Operation - this is a fallback)
        label = node.name

        if label:
            # Named constant - emit assignment
            # Note: Constant Operations don't carry value - they're pre-bound in context
            var_name = to_var_name(label)
            return CodeFragment(
                statements=[],
                bindings={const_id: var_name},
            )
        else:
            # Unnamed constant - should already be bound in context
            return CodeFragment.empty()

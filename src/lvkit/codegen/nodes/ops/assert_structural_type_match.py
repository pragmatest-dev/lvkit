"""Assert Structural Type Match (primResID 2416).

NI, "Assert Structural Type Match Function" (labview-api-ref,
``functions/assert-structural-type-match.html``): "Breaks the calling VI unless the
two input data types are identical, ignoring type definitions and type names. ...
This function does nothing at run time." and "LabVIEW ignores any run-time value
on the wire connected to this input": it checks types when the VI is edited, and
generated Python has nothing to run, so it writes no code.

Terminals (from the primitives entry): in 0 = anything, 1 = reference.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment


@register_op_fragment("ASSERT_STRUCTURAL_TYPE_MATCH")
def assert_structural_type_match(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    return CodeFragment.empty()

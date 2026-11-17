"""In Range and Coerce (primResID 1127) for real numbers and arrays of them.

NI ("In Range and Coerce Function", labview-api-ref
``functions/in-range-and-coerce.html``): coerced(x) is x, or the lower or upper
limit when x is outside them; "If the lower limit value is greater than the upper
limit value, In Range? is always FALSE and LabVIEW switches the lower limit value
and the upper limit value internally before computing coerced(x)"; an array x with
scalar limits is compared element by element.

Whether each limit is included in ``In Range?`` is the node's own setting, saved
in its ``objFlags``: bit 0x200000 is Include Lower Limit and bit 0x100000 is
Include Upper Limit. With no ``objFlags`` recorded the lower limit is included and
the upper limit excluded, the default the maintainer settled from forum posts
(2026-11-16). The flags affect only ``In Range?``; coerced(x) always clamps to
the closed range. UNVERIFIED, see ``docs/_internal/design/labview-binary-format.md``,
"In Range and Coerce records Include Lower/Upper Limit in the node's `objFlags`".

Terminals (from the primitives entry): in 2 = lower limit, 3 = x, 4 = upper limit;
out 0 = In Range?, out 1 = coerced(x).
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import is_real

# The node's own ``objFlags`` bits.
INCLUDE_LOWER_LIMIT = 0x200000
INCLUDE_UPPER_LIMIT = 0x100000
# A node with no ``objFlags`` includes the lower limit and excludes the upper.
DEFAULT_FLAGS = INCLUDE_LOWER_LIMIT

_COERCED = "max(min(in_2, in_4), min(in_3, max(in_2, in_4)))"


def _in_range(flags: int) -> str:
    """The comparison chain for the limits the flags include."""
    if not flags:
        flags = DEFAULT_FLAGS
    lower = "<=" if flags & INCLUDE_LOWER_LIMIT else "<"
    upper = "<=" if flags & INCLUDE_UPPER_LIMIT else "<"
    return f"in_2 {lower} in_3 {upper} in_4"


def _lifted(expr: str, node: PrimitiveNode) -> str:
    ranks = {
        f"in_{t.index}": rank
        for t in node.terminals
        if t.direction == "input" and (rank := array_rank(t.lv_type) or 0)
    }
    if not ranks:
        return expr
    lifted, _ = arrayify(parse_expr(expr), ranks, conditionals=True)
    return ast.unparse(lifted)


@register_op("IN_RANGE_AND_COERCE")
def in_range_and_coerce(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    numbers = [t for t in node.terminals if t.direction == "input"]
    if not numbers or not all(is_real(t.lv_type) for t in numbers):
        raise ConversionGap(
            "In Range and Coerce supports resolved real numbers and arrays of "
            "them only",
            node,
        )
    return {
        "in_range": _lifted(_in_range(node.obj_flags), node),
        "coercedx": _lifted(_COERCED, node),
    }

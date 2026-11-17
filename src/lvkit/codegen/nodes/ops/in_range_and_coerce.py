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
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import format_kind, is_orderable

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


def _unsupported(lv_type: LVType) -> str:
    """Why an input of ``lv_type`` has no Python: NI's Using Comparison Functions
    page takes any type but complex, path or refnum, and a cluster is compared in
    Compare Elements or Compare Aggregates mode, which the node's saved
    settings do not decode yet."""
    while lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        lv_type = lv_type.element_type
    kind = format_kind(lv_type).kind
    if kind == "cluster":
        return (
            "In Range and Coerce on a cluster: its Compare Elements/Compare "
            "Aggregates mode is not decoded"
        )
    return (
        f"In Range and Coerce on a {kind} input: it orders numbers, strings, "
        "Booleans and time stamps only"
    )


@register_op("IN_RANGE_AND_COERCE")
def in_range_and_coerce(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    for terminal in node.terminals:
        if terminal.direction != "input" or terminal.lv_type is None:
            continue
        if not is_orderable(terminal.lv_type):
            raise ConversionGap(_unsupported(terminal.lv_type), node)
    return {
        "in_range": _lifted(_in_range(node.obj_flags), node),
        "coercedx": _lifted(_COERCED, node),
    }

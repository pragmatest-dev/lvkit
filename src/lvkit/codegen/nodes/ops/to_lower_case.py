"""To Lower Case (prim 1189). in_1 = string -> out index 0 = lowercased.

The function is polymorphic (NI's To Lower Case page): a string array is lowered
element by element, one comprehension per dimension of the wired type."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("TO_LOWER_CASE")
def to_lower_case(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    rank = max(
        (
            array_rank(t.lv_type) or 0
            for t in node.terminals
            if t.direction == "input" and t.index == 1
        ),
        default=0,
    )
    if not rank:
        return {"lower": "in_1.lower()"}
    expr = f"_e{rank - 1}.lower()"
    for depth in reversed(range(rank)):
        source = "in_1" if depth == 0 else f"_e{depth - 1}"
        expr = f"[{expr} for _e{depth} in {source}]"
    return {"lower": expr}

"""To Variant (prim 8201).

in_1 = the data (any type) -> out index 0 = the variant holding it and its type.
The type is the type of the wire feeding the input, always in the VI; a Variant
wired in passes through unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from ...variant_values import as_variant, is_variant_type
from . import register_context_op
from ._flat_node import terminal

if TYPE_CHECKING:
    from ...context import CodeGenContext

_INPUT = 1


@register_context_op("TO_VARIANT")
def to_variant(
    node: PrimitiveNode, resolved: ResolvedPrimitive, ctx: CodeGenContext
) -> dict[str, str]:
    wired = terminal(node, "input", _INPUT)
    source = None
    if wired is not None and ctx.graph is not None:
        edges = ctx.graph.incoming_edges(wired.id)
        source = ctx.graph.get_terminal(edges[0].terminal_id) if edges else None
    lv_type = source.lv_type if source is not None else None
    if lv_type is None and wired is not None:
        lv_type = wired.lv_type
    if lv_type is None:
        raise ConversionGap("To Variant has no input of a known type", node)
    if is_variant_type(lv_type):
        return {"variant": "in_1"}
    return {"variant": as_variant(ctx, "in_1", lv_type)}

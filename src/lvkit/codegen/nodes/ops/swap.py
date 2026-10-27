"""Swap Bytes (prim 1162) and Swap Words (prim 1163).

Both act on an integer, an array of integers or a cluster, and "swap only the
integer elements of the cluster" (NI): how many bytes each integer has comes from
its type, so the type of the data input (index 1) is passed to the runtime as a
spec (``lvkit.runtime.flat``). The output (index 0) has the data's type.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op
from .type_cast import _spec


def _data_spec(node: PrimitiveNode) -> str | tuple:
    data = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1), None
    )
    return _spec(data.lv_type if data else None)


@register_op("SWAP_BYTES")
def swap_bytes(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"swapped": f"_lv.swap_bytes(in_1, {_data_spec(node)!r})"}


@register_op("SWAP_WORDS")
def swap_words(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"swapped": f"_lv.swap_words(in_1, {_data_spec(node)!r})"}

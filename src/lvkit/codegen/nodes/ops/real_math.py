"""Square Root (1060), Square (23059), Inverse Cosine (1207) and Polar To Re/Im
(23061) for real numbers and arrays of them.

NI ("Square Root Function", "Square Function", "Inverse Cosine Function",
"Polar To Re/Im Function", labview-api-ref ``functions/``): the square root of a
negative number is NaN; the square of x is "of the same numeric representation as
x"; arccos(x) is NaN for x below -1 or above 1; x = r*cos(theta) and
y = r*sin(theta), theta in radians. An input past what ``math`` accepts gives NaN
as LabVIEW does (``math.cos(inf)`` alone raises). A complex input has no code
generation yet.

Terminals (from the primitives entries): Square Root, Square, Inverse Cosine:
in 1 = x, out 0. Polar To Re/Im: in 3 = r (upper), in 2 = theta; out 1 = x,
out 0 = y.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.lv_type_codes import INTEGER_BYTE_WIDTHS, SIGNED_INTEGER_TYPE_NAMES
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import _scalar_underlying, is_real

_SQRT = "(math.sqrt(in_1) if in_1 >= 0 else math.nan)"
_ARCCOS = "(math.acos(in_1) if -1 <= in_1 <= 1 else math.nan)"
_POLAR_X = "(in_3 * math.cos(in_2) if math.isfinite(in_2) else math.nan)"
_POLAR_Y = "(in_3 * math.sin(in_2) if math.isfinite(in_2) else math.nan)"


def _require_real(node: PrimitiveNode, name: str) -> None:
    if not node.terminals or not all(is_real(t.lv_type) for t in node.terminals):
        raise ConversionGap(
            f"{name} supports resolved real numbers and arrays of them only", node
        )


def _ranks(node: PrimitiveNode) -> dict[str, int]:
    """The dimension count of each wired array input, by template name."""
    return {
        f"in_{t.index}": rank
        for t in node.terminals
        if t.direction == "input" and (rank := array_rank(t.lv_type) or 0)
    }


def _lifted(expr: str, node: PrimitiveNode) -> str:
    ranks = _ranks(node)
    if not ranks:
        return expr
    lifted, _ = arrayify(parse_expr(expr), ranks, conditionals=True)
    return ast.unparse(lifted)


@register_op("SQUARE_ROOT")
def square_root(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    _require_real(node, "Square Root")
    return _lifted(_SQRT, node)


@register_op("INVERSE_COSINE")
def inverse_cosine(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    _require_real(node, "Inverse Cosine")
    return _lifted(_ARCCOS, node)


@register_op("SQUARE")
def square(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    """An integer wraps at its width, as LabVIEW integers do."""
    _require_real(node, "Square")
    name = _scalar_underlying(
        next(t.lv_type for t in node.terminals if t.direction == "input")
    )
    bits = INTEGER_BYTE_WIDTHS.get(name or "", 0) * 8
    if not bits:
        return _lifted("in_1 * in_1", node)
    if name in SIGNED_INTEGER_TYPE_NAMES:
        half = 1 << (bits - 1)
        return _lifted(f"(in_1 * in_1 + {half}) % {1 << bits} - {half}", node)
    return _lifted(f"in_1 * in_1 % {1 << bits}", node)


@register_op("POLAR_TO_RE_IM")
def polar_to_re_im(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    _require_real(node, "Polar To Re/Im")
    return {"x": _lifted(_POLAR_X, node), "y": _lifted(_POLAR_Y, node)}

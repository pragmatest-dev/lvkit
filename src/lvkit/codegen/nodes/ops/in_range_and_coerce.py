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

A cluster x is compared in Compare Elements mode: NI ("Using Comparison
Functions", ``labview/page/using-comparison-functions.html``) says its functions
"compare an array or cluster of a data type to a scalar of the same data type and
produce an array or cluster of Boolean values"; Compare Aggregates needs clusters
of the same number of elements. Three corpus nodes compare a cluster to scalar
limits, which only Compare Elements allows, and every corpus node has the same
``objFlags`` apart from the include-upper bit, so all are in that mode (see the
In Range entry of ``docs/_internal/design/labview-binary-format.md``). In Range?
is then a cluster of Booleans shaped like x and coerced(x) a cluster with each
field clamped, each field against the matching field of a cluster limit or against
a scalar limit (``flat.walk_operands``, the walk of arrays and clusters). Compare
Aggregates is never generated.

Terminals (from the primitives entry): in 2 = lower limit, 3 = x, 4 = upper limit;
out 0 = In Range?, out 1 = coerced(x).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from ...flat import Functions, Layout, UnsupportedLayout, layout_of, walk_operands
from . import register_context_op
from ._numeric import format_kind, unorderable_leaf

if TYPE_CHECKING:
    from ...context import CodeGenContext

# The node's own ``objFlags`` bits.
INCLUDE_LOWER_LIMIT = 0x200000
INCLUDE_UPPER_LIMIT = 0x100000
# A node with no ``objFlags`` includes the lower limit and excludes the upper.
DEFAULT_FLAGS = INCLUDE_LOWER_LIMIT

_COERCED = "max(min({lower}, {upper}), min({x}, max({lower}, {upper})))"
# The operands of a cluster walk, in this order (x fixes the shape).
_OPERANDS = (3, 2, 4)


def _in_range(flags: int) -> str:
    """The comparison chain for the limits the flags include."""
    if not flags:
        flags = DEFAULT_FLAGS
    lower = "<=" if flags & INCLUDE_LOWER_LIMIT else "<"
    upper = "<=" if flags & INCLUDE_UPPER_LIMIT else "<"
    return f"{{lower}} {lower} {{x}} {upper} {{upper}}"


def _scalar(template: str) -> str:
    return template.format(x="in_3", lower="in_2", upper="in_4")


def _holds_cluster(lv_type: LVType) -> bool:
    while lv_type.kind == LVTypeKind.ARRAY and lv_type.element_type is not None:
        lv_type = lv_type.element_type
    return lv_type.kind == LVTypeKind.CLUSTER


def _layout(node: PrimitiveNode, direction: str, index: int) -> Layout:
    term = next(
        (t for t in node.terminals if t.direction == direction and t.index == index),
        None,
    )
    try:
        return layout_of(term.lv_type if term else None)
    except UnsupportedLayout as error:
        raise ConversionGap(
            f"In Range and Coerce on a cluster: its {direction} {index} is {error}",
            node,
        ) from error


def _compare_elements(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    template: str,
    kind: str,
    flags: int,
    output: int,
) -> str:
    """``template`` applied to each field of a cluster x (and of cluster limits,
    or the scalar limit against every field), recursing through arrays and
    clusters. The result is built in the type of output terminal ``output``."""
    operands = [(_layout(node, "input", i), f"in_{i}") for i in _OPERANDS]

    def leaf(expressions: list[str]) -> str:
        x, lower, upper = expressions
        return template.format(x=x, lower=lower, upper=upper)

    try:
        return walk_operands(
            operands,
            leaf,
            kind,
            (template, flags),
            0,
            Functions(ctx, ">"),
            _layout(node, "output", output),
        )
    except UnsupportedLayout as error:
        raise ConversionGap(
            "In Range and Coerce on a cluster: x and the limits must be the same "
            f"shape of clusters and arrays, or a limit a single value ({error})",
            node,
        ) from error


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


def _unsupported(leaf: LVType) -> str:
    """Why a value of type ``leaf`` has no Python: NI's Using Comparison Functions
    page takes any type but complex, path or refnum."""
    kind = format_kind(leaf).kind
    return (
        f"In Range and Coerce on a {kind} input: it orders numbers, strings, "
        "Booleans and time stamps only"
    )


@register_context_op("IN_RANGE_AND_COERCE")
def in_range_and_coerce(
    node: PrimitiveNode, resolved: ResolvedPrimitive, ctx: CodeGenContext
) -> dict[str, str]:
    clusters = False
    for terminal in node.terminals:
        if terminal.direction != "input" or terminal.lv_type is None:
            continue
        if (leaf := unorderable_leaf(terminal.lv_type)) is not None:
            raise ConversionGap(_unsupported(leaf), node)
        clusters = clusters or _holds_cluster(terminal.lv_type)
    in_range, coerced = _in_range(node.obj_flags), _COERCED
    if clusters:
        return {
            "in_range": _compare_elements(
                node, ctx, in_range, "in_range", node.obj_flags, 0
            ),
            "coercedx": _compare_elements(node, ctx, coerced, "coerced", 0, 1),
        }
    return {
        "in_range": _lifted(_scalar(in_range), node),
        "coercedx": _lifted(_scalar(coerced), node),
    }

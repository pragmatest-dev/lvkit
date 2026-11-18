"""``Enum to Array of Enums.vim`` (vi.lib numeric, Conversion palette).

NI, "Enum to Array of Enums": "Creates an array of enums with the same number of
elements as values in the enum." ``array of enums`` "returns an array containing
each enum value in order. For example, if enum has values {cat, dog, bird}, array
of enums returns a three-element array with values cat, dog, and bird,
respectively." The result depends on the enum's type alone, never on the value
wired in, so the array is the list of the item values the wired enum's type
records, written into the call in item order (an enum's value in generated code
is the integer of its item).
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import LVTypeKind, TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..output_binding import bind_output
from .grpc_pane import terminal_of


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of ``Enum to Array of Enums.vim``: the item values of the enum the
    ``enum`` input carries."""
    out = terminal_of(node, entry, "array_of_enums", TerminalDirection.OUTPUT)
    if out is None or not ctx.is_wired(out.id):
        return CodeFragment.empty()
    source = terminal_of(node, entry, "enum", TerminalDirection.INPUT)
    lv_type = source.lv_type if source is not None else None
    if lv_type is None or lv_type.kind != LVTypeKind.ENUM or not lv_type.values:
        raise ConversionGap(
            "the enum input is not recorded as an enum with its items, so the "
            "values of the enum are not known",
            node,
        )
    values = sorted(item.value for item in lv_type.values.values())
    return bind_output(
        node, out.id, "array_of_enums", parse_expr(repr(values)), ctx
    )

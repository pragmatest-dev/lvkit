"""Format Value (primResID 1512).

NI, "Format Value Function" (labview-api-ref, ``functions/format-value.html``):
"Converts a number into a regular string according to the format specified in
format string and appends this to string. ... The Format Into String function has
the same functionality as Format Value but can use multiple inputs."

So the node is Format Into String with one value: the same rules write its text
(``codegen/nodes/printf.py``, ``codegen/labview_format.py``). Format Value's
terminals are numbered differently, so the node is handed to that generator with
its inputs under Format Into String's numbers: format string 2 -> 0, string 3 ->
1, value 1 -> 5.

NI gives no format for a Format Value whose ``format string`` is unwired, so that
is a gap.

Terminals (from the primitives entry): in 1 = value, 2 = format string, 3 =
string; out 0 = output string.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from .. import printf
from . import register_op_fragment
from ._flat_node import terminal

# Format Value input index -> Format Into String input index.
_AS_FORMAT_INTO_STRING = {2: 0, 3: 1, 1: 5}


@register_op_fragment("FORMAT_VALUE")
def format_value(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    format_string = terminal(node, "input", 2)
    if format_string is None or not ctx.is_wired(format_string.id):
        raise ConversionGap(
            "Format Value with no format string: NI's page gives no default format",
            node,
        )
    as_format_into_string = node.model_copy(
        update={
            "terminals": [
                t.model_copy(update={"index": _AS_FORMAT_INTO_STRING[t.index]})
                if t.direction == "input"
                else t
                for t in node.terminals
            ]
        }
    )
    return printf.generate(as_format_into_string, ctx)

"""Decimal String To Number (primResID 1184).

NI, "Decimal String To Number Function" (labview-api-ref,
``functions/decimal-string-to-number.html``): "Converts the numeric characters in
string, starting at offset, to a decimal integer and returns it in number."

* ``offset``: "If offset is unwired or less than 0, the function takes offset as
  0."
* ``default``: "any object of numeric representation and specifies the numeric
  representation for number. The default is a 32-bit signed integer value of 0."
  "default is used since no digits were read" (``a49b``, default -9 gives -9 and
  offset past number 0).
* ``offset past number``: "the index in string of the first character following
  the number" (``13ax`` gives 13 and 2).
* "Because an integer is being converted, conversion stops at the decimal point"
  (``-4.8bcde`` gives -4 and 2), also into a floating-point ``default``.
* "If number is an integer, it can overflow if the input is out of range. In that
  case, number is set to the maximum or minimum value for the data type" (300 into
  an 8-bit signed integer is 127).

NI's page does not say what the function does with a leading space or a plus
sign. A character that is not a digit stops the conversion, so a leading space
reads no digits; a plus sign is read as a minus sign is. With no digits read,
``offset past number`` is ``offset``.

Only a string input is converted (NI also takes an array or cluster of strings).

Terminals (from the primitives entry): in 2 = default, 3 = offset, 4 = string;
out 0 = number, 1 = offset past number.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._numeric import FormatKind
from ._string_number import integer_range, string_to_number_fragment

_TRANSLATOR = "_decimal_string_to_number"


def decimal_string_to_number(string, offset, default, low=None, high=None):
    start = max(offset, 0)
    end = start
    if string[end : end + 1] in ("+", "-"):
        end += 1
    digits = end
    while string[end : end + 1] in tuple("0123456789"):
        end += 1
    if end == digits:
        return default, start
    number = int(string[start:end])
    if low is not None and high is not None:
        number = min(max(number, low), high)
    return number, end


@register_op_fragment("DECIMAL_STRING_TO_NUMBER")
def decimal_string_to_number_node(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    def limits(kind: FormatKind) -> str:
        if kind.kind != "int":
            return ""
        low, high = integer_range(kind)
        return f"{low}, {high}"

    return string_to_number_fragment(
        node,
        ctx,
        "Decimal String To Number",
        _TRANSLATOR,
        decimal_string_to_number,
        (2, 3, 4),
        limits,
    )

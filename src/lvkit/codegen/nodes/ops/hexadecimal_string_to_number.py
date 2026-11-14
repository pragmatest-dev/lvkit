"""Hexadecimal String To Number (primResID 1185).

NI, "Hexadecimal String To Number Function" (labview-api-ref,
``functions/hexadecimal-string-to-number.html``): "Interprets the characters 0
through 9, A through F, and a through f in string starting at offset as a hex
integer and returns it in number."

* ``offset``: "If offset is unwired or less than 0, the function takes offset as 0."
* ``default``: "any object of numeric representation and specifies the numeric
  representation for number. The default is a 32-bit unsigned integer value of 0."
* ``offset past number``: "the index in string of the first character following
  the number"; NI's rows: ``3ab`` at 0 gives 939 and 3; at 2 gives 11 and 3;
  ``f3g`` gives 243 and 2, "g is not a valid hex character, so conversion stops
  there".
* "If the input string represents a number outside the range of the representation
  of number, number is set to the maximum value for that data type" (``1FFFFFFFF``
  into a 32-bit unsigned integer is 4294967295).
* "Negative numbers are not permitted for hex": ``-30`` gives the default and an
  offset past number of 0, so no sign is read and a string with no hex digit gives
  the default and ``offset``.

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

_TRANSLATOR = "_hexadecimal_string_to_number"


def hexadecimal_string_to_number(string, offset, default, low=None, high=None):
    start = max(offset, 0)
    end = start
    while string[end : end + 1] in tuple("0123456789abcdefABCDEF"):
        end += 1
    if end == start:
        return default, start
    number = int(string[start:end], 16)
    if high is not None:
        number = min(number, high)
    return number, end


@register_op_fragment("HEXADECIMAL_STRING_TO_NUMBER")
def hexadecimal_string_to_number_node(
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
        "Hexadecimal String To Number",
        _TRANSLATOR,
        hexadecimal_string_to_number,
        (2, 3, 4),
        limits,
    )

"""Fract/Exp String To Number (primResID 1187).

NI, "Fract/Exp String To Number Function" (labview-api-ref,
``functions/fract-exp-string-to-number.html``): "Interprets the characters 0
through 9, plus, minus, e, E, and the decimal point (usually period) in string
starting at offset as a floating-point number in engineering notation,
exponential, or fractional format and returns it in number."

* ``use system decimal point (T)``: "If TRUE (default), the decimal separator uses
  the localized decimal separator. If FALSE, the decimal separator is a period":
  ``locale.localeconv()['decimal_point']``, else ``.``.
* ``offset``: "If offset is unwired or less than 0, the function takes offset as
  0."
* ``string``: "If string contains the characters Inf or NaN, this function returns
  the LabVIEW values Inf and NaN."
* NI's rows: ``-4.7e-3x`` gives -0.0047 and 7 ("x is not allowed, so conversion
  stops there"); ``+5.3.2`` gives 5.3 and 4 ("Second decimal point not allowed").
  An exponent mark with no digits after it is not part of the number.
* "If you scan a string that does not fit into the numeric data type you specify,
  this function returns the largest number that fits into that data type": an
  integer ``default`` gives the number rounded to the nearest integer (halves to
  even) within the type's range; a floating-point result too large for a double is
  the largest double.
* ``default``: "any object of numeric representation and specifies the numeric
  representation for number"; NI's page has no row for a string with no number in
  it: as for Decimal and Hexadecimal String To Number (the ``-30`` row of the
  latter), it gives the default and ``offset`` as the offset past number. NaN into
  an integer is not described: Python's ``round`` raises ``ValueError``.

Only a string input is converted (NI also takes an array or cluster of strings).

Terminals (from the primitives entry): in 2 = use system decimal point, 3 = default,
4 = offset, 5 = string; out 0 = number, 1 = offset past number.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._numeric import FormatKind
from ._regex_node import input_expression
from ._string_number import integer_range, string_to_number_fragment

_TRANSLATOR = "_fract_exp_string_to_number"


def fract_exp_string_to_number(string, offset, default, point, low=None, high=None):
    start = max(offset, 0)
    end = start
    if string[end : end + 1] in ("+", "-"):
        end += 1
    for special in ("Inf", "NaN"):
        if string.startswith(special, end):
            end += 3
            number = float(string[start:end])
            break
    else:
        digits = end
        while string[end : end + 1] in tuple("0123456789"):
            end += 1
        found = end > digits
        if string[end : end + len(point)] == point:
            end += len(point)
            fraction = end
            while string[end : end + 1] in tuple("0123456789"):
                end += 1
            found = found or end > fraction
        if not found:
            return default, start
        if string[end : end + 1] in ("e", "E"):
            mark = end + 1
            if string[mark : mark + 1] in ("+", "-"):
                mark += 1
            exponent = mark
            while string[mark : mark + 1] in tuple("0123456789"):
                mark += 1
            if mark > exponent:
                end = mark
        number = float(string[start:end].replace(point, "."))
        if number in (float("inf"), float("-inf")):
            number = 1.7976931348623157e308 * (1 if number > 0 else -1)
    if low is not None and high is not None:
        if number in (float("inf"), float("-inf")):
            number = low if number < 0 else high
        else:
            number = min(max(round(number), low), high)
    return number, end


@register_op_fragment("FRACT_EXP_STRING_TO_NUMBER")
def fract_exp_string_to_number_node(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    system = input_expression(ctx, node, 2)
    point = (
        "locale.localeconv()['decimal_point']"
        if system is None
        else (f"(locale.localeconv()['decimal_point'] if {system} else '.')")
    )
    ctx.add_import("import locale")

    def limits(kind: FormatKind) -> str:
        if kind.kind != "int":
            return point
        low, high = integer_range(kind)
        return f"{point}, {low}, {high}"

    return string_to_number_fragment(
        node,
        ctx,
        "Fract/Exp String To Number",
        _TRANSLATOR,
        fract_exp_string_to_number,
        (3, 4, 5),
        limits,
    )

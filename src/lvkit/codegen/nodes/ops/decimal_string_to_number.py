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

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_string_type_name

from ...ast_utils import build_assign, build_multi_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._numeric import format_kind
from ._regex_node import declare_translator, input_expression

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
    string_term = terminal(node, "input", 4)
    number_term = terminal(node, "output", 0)
    past_term = terminal(node, "output", 1)
    if (
        string_term is None
        or string_term.lv_type is None
        or not is_string_type_name(string_term.lv_type.underlying_type)
    ):
        raise ConversionGap(
            "Decimal String To Number converts a string; an array or cluster of "
            "strings has no code generation yet",
            node,
        )
    kind = format_kind(number_term.lv_type if number_term is not None else None)
    if kind.kind not in ("int", "float"):
        raise ConversionGap(
            f"Decimal String To Number into {kind.kind} has no code generation yet",
            node,
        )
    string = input_expression(ctx, node, 4) or "''"
    offset = input_expression(ctx, node, 3) or "0"
    default = input_expression(ctx, node, 2) or "0"
    arguments = f"{string}, {offset}, {default}"
    if kind.kind == "int":
        low = -(2 ** (kind.bits - 1)) if kind.signed else 0
        high = 2 ** (kind.bits - 1) - 1 if kind.signed else 2**kind.bits - 1
        arguments += f", {low}, {high}"
    function = declare_translator(ctx, _TRANSLATOR, decimal_string_to_number)
    number = ctx.make_output_var(
        "number", node.id, terminal_id=number_term.id if number_term else None
    )
    past = ctx.make_output_var(
        "offset_past_number", node.id, terminal_id=past_term.id if past_term else None
    )
    call = f"{function}({arguments})"
    statements: list[ast.stmt] = [build_multi_assign([number, past], parse_expr(call))]
    if kind.kind == "float":
        statements.append(build_assign(number, parse_expr(f"float({number})")))
    bindings: dict[str, str] = {}
    if number_term is not None:
        bindings[number_term.id] = number
    if past_term is not None:
        bindings[past_term.id] = past
    return CodeFragment(statements=statements, bindings=bindings)

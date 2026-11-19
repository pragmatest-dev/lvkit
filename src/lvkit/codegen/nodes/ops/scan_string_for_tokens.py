"""Scan String For Tokens (primResID 8101).

Returns the next token of ``input string`` from ``offset``: the token, its index
in ``operators`` (-1 for any other token, -2 at the end of the string) and the
offset past it and its trailing delimiters. The rules are in
``codegen/scan_tokens.py``; the function that applies them is written into the
generated module.

Left as a gap, each for what NI's page and the forum reports found do not settle:
``allow empty tokens?`` wired to anything but a constant FALSE; ``operators``
that are not constants, or that hold a ``%`` format code or a ``+`` or ``-`` (NI:
"If the strings + or - are defined as operators, the function does not recognize
leading, or unary, + and - signs", an exception to the longest match whose
extent the page does not give).

Terminals (from the primitives entry):
  in  4 = use cached delim/oper data? (a speed hint only), 5 = allow empty
      tokens?, 6 = delimiters, 7 = operators, 8 = offset (default 0),
      9 = input string
  out 0 = token index, 1 = token string, 2 = offset past token, 3 = string out
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...scan_tokens import DEFAULT_DELIMITERS, scan_string_for_tokens
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import declare_translator, input_expression, literal

_FUNCTION_NAME = "_scan_string_for_tokens"

_OUTPUTS = {
    0: "token_index",
    1: "token_string",
    2: "offset_past_token",
    3: "string_out",
}


def _gap(node: PrimitiveNode, reason: str) -> ConversionGap:
    return ConversionGap(f"Scan String For Tokens {reason}", node)


def _operators(ctx: CodeGenContext, node: PrimitiveNode) -> str:
    text = input_expression(ctx, node, 7)
    if text is None:
        return "[]"
    operators = literal(text)
    if not isinstance(operators, list | tuple) or not all(
        isinstance(op, str) for op in operators
    ):
        raise _gap(node, "needs its operators to be constants")
    if any("%" in op or op in ("+", "-") for op in operators):
        raise _gap(
            node,
            "has operators with a format code (%d, %x, ...) or a + or - sign, "
            "whose scanning NI's page does not fully give",
        )
    return text


@register_op_fragment("SCAN_STRING_FOR_TOKENS")
def scan_string_for_tokens_op(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    allow_empty = input_expression(ctx, node, 5)
    if allow_empty is not None and literal(allow_empty) is not False:
        raise _gap(
            node,
            "has allow empty tokens? wired to something other than a constant "
            "FALSE; NI's page does not say how a leading or trailing delimiter "
            "reads then",
        )
    operators = _operators(ctx, node)

    wired = [
        index
        for index in _OUTPUTS
        if (term := terminal(node, "output", index)) is not None
        and ctx.is_wired(term.id)
    ]
    if not wired:
        return CodeFragment.empty()

    function = declare_translator(ctx, _FUNCTION_NAME, scan_string_for_tokens)
    string = input_expression(ctx, node, 9) or "''"
    offset = input_expression(ctx, node, 8) or "0"
    delimiters = input_expression(ctx, node, 6) or repr(DEFAULT_DELIMITERS)

    statements: list[ast.stmt] = []
    scanned = ctx.make_output_var("scanned_token", node.id)
    statements.append(
        build_assign(
            scanned,
            parse_expr(f"{function}({string}, {offset}, {delimiters}, {operators})"),
        )
    )
    expressions = {
        0: f"{scanned}[0]",
        1: f"{scanned}[1]",
        2: f"{scanned}[2]",
        3: string,
    }
    bindings: dict[str, str] = {}
    for index in wired:
        term = terminal(node, "output", index)
        assert term is not None
        variable = ctx.make_output_var(_OUTPUTS[index], node.id, terminal_id=term.id)
        statements.append(build_assign(variable, parse_expr(expressions[index])))
        bindings[term.id] = variable
    return CodeFragment(statements=statements, bindings=bindings)

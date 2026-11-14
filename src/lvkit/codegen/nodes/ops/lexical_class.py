"""Lexical Class (primResID 1126) of a scalar string or number.

NI, "Lexical Class Function" (labview-api-ref, ``functions/lexical-class.html``):
"Returns the class number for char. If char is a string, this function uses the
first byte in the string. If char is a number, this function interprets it as the
ASCII value of a character." The classes: 0 extended characters (128 through 255);
1 non-displayable ASCII characters (0 to 31 excluding 9 through 13); 2 white space
(32 and 9 through 13); 3 digits; 4 uppercase; 5 lowercase; 6 all other printable
ASCII.

NI lists code 127 in no class; it is non-displayable ASCII, so class 1. NI does not
say what an empty string gives: ``in[0]`` raises ``IndexError``. An array or cluster
of characters has no code generation yet.

Terminals (from the primitives entry): in 1 = char; out 0 = class number.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import is_string_type_name
from lvkit.models import LVTypeKind

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._numeric import is_real
from ._regex_node import input_expression, string_variable

_CLASS = (
    "(0 if {c} >= 128 else 2 if {c} == 32 or 9 <= {c} <= 13 else "
    "1 if {c} < 32 or {c} == 127 else 3 if 48 <= {c} <= 57 else "
    "4 if 65 <= {c} <= 90 else 5 if 97 <= {c} <= 122 else 6)"
)


@register_op_fragment("LEXICAL_CLASS")
def lexical_class(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    char = terminal(node, "input", 1)
    out = terminal(node, "output", 0)
    text = input_expression(ctx, node, 1)
    if char is None or char.lv_type is None or text is None:
        raise ConversionGap("Lexical Class has no char wired", node)
    lv_type = char.lv_type
    statements: list[ast.stmt] = []
    if lv_type.kind == LVTypeKind.PRIMITIVE and is_string_type_name(
        lv_type.underlying_type
    ):
        source = string_variable(ctx, node, text, statements, "char")
        code_text = f"ord({source}[0])"
    elif lv_type.kind == LVTypeKind.PRIMITIVE and is_real(lv_type):
        code_text = f"int({text})"
    else:
        raise ConversionGap(
            "Lexical Class of an array or cluster of characters has no code "
            "generation yet",
            node,
        )
    code = ctx.make_output_var("char_code", node.id)
    statements.append(build_assign(code, parse_expr(code_text)))
    bindings: dict[str, str] = {}
    if out is not None and ctx.is_wired(out.id):
        name = ctx.make_output_var("class_number", node.id, terminal_id=out.id)
        statements.append(build_assign(name, parse_expr(_CLASS.format(c=code))))
        bindings[out.id] = name
    return CodeFragment(statements=statements, bindings=bindings)

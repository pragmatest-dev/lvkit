"""Match True/False String (primResID 1533).

NI, "Match True/False String Function" (labview-api-ref,
``functions/match-true-false-string.html``): "Examines the beginning of string to
see whether it matches true string or false string." ``selection`` "is TRUE if true
string matches the beginning of string, and FALSE if false string matches the
beginning of string, or if neither string matches." ``output string`` "is a
shortened version of string with the matching substring removed if either true
string or false string matches the beginning of string. If neither true string nor
false string matches the beginning of string, output string returns string
unchanged." ``str.startswith`` is the match; the true string is tried first. NI
gives no default for ``true string`` or ``false string``, so an unwired one is a
gap (its unknown default).

Terminals (from the primitives entry): in 2 = false string, 3 = true string, 4 =
string; out 0 = selection, 1 = output string.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...errors import UnknownDefault
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable


@register_op_fragment("MATCH_TRUE_FALSE_STRING")
def match_true_false_string(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    string = string_variable(
        ctx, node, input_expression(ctx, node, 4) or "''", statements, "string"
    )
    names = {}
    for index, label in ((3, "true string"), (2, "false string")):
        text = input_expression(ctx, node, index)
        if text is None:
            raise UnknownDefault("Match True/False String", label, node)
        names[index] = string_variable(
            ctx, node, text, statements, label.replace(" ", "_")
        )
    true, false = names[3], names[2]
    matched = ctx.make_output_var("matched", node.id)
    statements.append(
        build_assign(
            matched,
            parse_expr(
                f"{true} if {string}.startswith({true}) else "
                f"{false} if {string}.startswith({false}) else ''"
            ),
        )
    )
    bindings: dict[str, str] = {}
    selection = terminal(node, "output", 0)
    if selection is not None and ctx.is_wired(selection.id):
        name = ctx.make_output_var("selection", node.id, terminal_id=selection.id)
        statements.append(
            build_assign(name, parse_expr(f"{string}.startswith({true})"))
        )
        bindings[selection.id] = name
    shortened = terminal(node, "output", 1)
    if shortened is not None and ctx.is_wired(shortened.id):
        name = ctx.make_output_var("output_string", node.id, terminal_id=shortened.id)
        statements.append(build_assign(name, parse_expr(f"{string}[len({matched}):]")))
        bindings[shortened.id] = name
    return CodeFragment(statements=statements, bindings=bindings)

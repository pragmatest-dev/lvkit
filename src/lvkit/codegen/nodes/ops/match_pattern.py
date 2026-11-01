"""Match Pattern (primResID 1535).

Searches ``string`` from ``offset`` for the first match of ``regular expression``
and returns the text before it, the match, the text after it and the offset past
it (the whole string, two empty strings and -1 with no match). The pattern is
written in Match Pattern's own syntax (``codegen/match_pattern_regex.py``).

A pattern that is a constant is translated when the code is generated and written
as an ``re`` pattern; a pattern computed at run time is translated by the function
written into the generated module. The search runs on ``string[start:]`` rather
than with ``re``'s ``pos`` so a leading ``^`` anchors at the offset.

Terminals (from the primitives entry):
  in  0 = string, 1 = regular expression, 2 = offset (default 0)
  out 3 = before substring, 4 = match substring, 5 = after substring,
      6 = offset past match
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...match_pattern_regex import to_python_regex
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import (
    declare_translator,
    input_expression,
    is_wired_output,
    literal,
    search_start,
    string_variable,
)

_TRANSLATOR_NAME = "_match_pattern_regex"

_OUTPUTS = {
    3: "before_substring",
    4: "match_substring",
    5: "after_substring",
    6: "offset_past_match",
}


@register_op_fragment("MATCH_PATTERN")
def match_pattern(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    wired = [index for index in _OUTPUTS if is_wired_output(ctx, node, index)]
    if not wired:
        return CodeFragment.empty()

    statements: list[ast.stmt] = []
    string = string_variable(
        ctx, node, input_expression(ctx, node, 0) or "''", statements
    )

    pattern = input_expression(ctx, node, 1)
    constant = literal(pattern) if pattern is not None else ""
    if isinstance(constant, str):
        regex = repr(to_python_regex(constant))
    else:
        translate = declare_translator(ctx, _TRANSLATOR_NAME, to_python_regex)
        regex = f"{translate}({pattern})"

    start = search_start(ctx, node, input_expression(ctx, node, 2), statements)
    subject = string if start is None else f"{string}[{start}:]"

    found = ctx.make_output_var("pattern_match", node.id)
    statements.append(build_assign(found, parse_expr(f"re.search({regex}, {subject})")))

    def shifted(position: str) -> str:
        return position if start is None else f"{start} + {position}"

    expressions = {
        3: f"{string} if {found} is None else {string}[:{shifted(found + '.start()')}]",
        4: f"'' if {found} is None else {found}.group()",
        5: f"'' if {found} is None else {string}[{shifted(found + '.end()')}:]",
        6: f"-1 if {found} is None else {shifted(found + '.end()')}",
    }
    bindings: dict[str, str] = {}
    for index in wired:
        term = terminal(node, "output", index)
        assert term is not None
        variable = ctx.make_output_var(_OUTPUTS[index], node.id, terminal_id=term.id)
        statements.append(build_assign(variable, parse_expr(expressions[index])))
        bindings[term.id] = variable
    return CodeFragment(statements=statements, bindings=bindings, imports={"import re"})

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
from ...match_pattern_regex import function_source, to_python_regex
from . import register_op_fragment
from ._flat_node import terminal

_TRANSLATOR_NAME = "_match_pattern_regex"


def _input(ctx: CodeGenContext, node: PrimitiveNode, index: int) -> str | None:
    """The expression feeding input ``index``; None when nothing resolves to it
    (the input is unwired, or its source has no variable)."""
    term = terminal(node, "input", index)
    return ctx.resolve(term.id) if term is not None else None


def _literal(text: str | None) -> object | None:
    """The value of an expression that is a literal; None for anything else."""
    if text is None:
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


@register_op_fragment("MATCH_PATTERN")
def match_pattern(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    outputs = {
        index: term
        for index in (3, 4, 5, 6)
        if (term := terminal(node, "output", index)) is not None
        and ctx.is_wired(term.id)
    }
    if not outputs:
        return CodeFragment.empty()

    statements: list[ast.stmt] = []
    string = _input(ctx, node, 0) or "''"
    if not string.isidentifier():
        text = ctx.make_output_var("string", node.id)
        statements.append(build_assign(text, parse_expr(string)))
        string = text

    pattern = _input(ctx, node, 1)
    constant = _literal(pattern) if pattern is not None else ""
    if isinstance(constant, str):
        regex = repr(to_python_regex(constant))
    else:
        translate = ctx.ctypes_module.declare(
            ("helper", _TRANSLATOR_NAME), _TRANSLATOR_NAME, function_source
        )
        regex = f"{translate}({pattern})"

    offset = _input(ctx, node, 2)
    start = _literal(offset) if offset is not None else 0
    if isinstance(start, int) and not isinstance(start, bool):
        start_expr = str(max(start, 0))
    else:
        start_expr = f"max({offset}, 0)"
    if start_expr == "0":
        subject = string
        start_name = None
    else:
        start_name = ctx.make_output_var("search_start", node.id)
        statements.append(build_assign(start_name, parse_expr(start_expr)))
        subject = f"{string}[{start_name}:]"

    found = ctx.make_output_var("pattern_match", node.id)
    statements.append(build_assign(found, parse_expr(f"re.search({regex}, {subject})")))

    def shifted(position: str) -> str:
        return position if start_name is None else f"{start_name} + {position}"

    expressions = {
        3: f"{string} if {found} is None else {string}[:{shifted(found + '.start()')}]",
        4: f"'' if {found} is None else {found}.group()",
        5: f"'' if {found} is None else {string}[{shifted(found + '.end()')}:]",
        6: f"-1 if {found} is None else {shifted(found + '.end()')}",
    }
    labels = {
        3: "before_substring",
        4: "match_substring",
        5: "after_substring",
        6: "offset_past_match",
    }
    bindings: dict[str, str] = {}
    for index in sorted(outputs):
        variable = ctx.make_output_var(
            labels[index], node.id, terminal_id=outputs[index].id
        )
        statements.append(build_assign(variable, parse_expr(expressions[index])))
        bindings[outputs[index].id] = variable
    return CodeFragment(statements=statements, bindings=bindings, imports={"import re"})

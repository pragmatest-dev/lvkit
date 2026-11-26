"""Match Regular Expression (the XNode whose library is
``<vilib>/regexp/Match Regular Expression.xnode``; ``nodes/xnode.py`` dispatches
here by that recorded path).

NI, "Match Regular Expression" (labview-api-ref,
``vi-lib/regexp/match-regular-expression-xnode.html``): "Searches for a regular
expression in the input string beginning at the offset you enter. If the function
finds a match, it splits the string into three substrings and any number of
submatches." If it finds none, "whole match and after match contain empty strings,
before match contains the entire input string, offset past match returns -1, and
all submatches outputs return empty strings". ``multiline?`` (default False) makes
``^`` and ``$`` match at every line, ``ignore case?`` (default False) makes the
search case insensitive, and the submatches are the parenthesised groups in the
order their opening parenthesis appears (nested ones after their outer group).
"Regular expression support is provided by the PCRE library package"; Python's
``re`` reads the syntax PCRE and Perl share. A pattern ``re`` does not accept
(NI's "Regular Expression Error Codes" lists PCRE's own) is the node's error out,
carrying ``re``'s message that names the construct; a null character in either
string is error -4702.

Terminals (the XNode's own pane): in 0 input string, 1 regular expression,
6 offset, 9 ignore case?, 10 multiline?; out 2 before match, 3 whole match,
4 after match, 5 offset past match, then one output per submatch; 7 error in and
8 error out (``catch_failures`` makes them).
"""

from __future__ import annotations

import ast
import hashlib
import re

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import NativeError

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..native_failure import CatalogFailures, catch_failures

MATCH_REGULAR_EXPRESSION_PATH = "<vilib>/regexp/Match Regular Expression.xnode"

_STRING_IN, _PATTERN_IN, _OFFSET_IN, _IGNORE_CASE_IN, _MULTILINE_IN = 0, 1, 6, 9, 10
_BEFORE, _WHOLE, _AFTER, _OFFSET_PAST, _ERROR_OUT = 2, 3, 4, 5, 8
_NAME = "Match Regular Expression"
# NI, "Regular Expression Error Codes": -4702 "One of the string parameters
# contains a null character."
_NULL_CHARACTER = NativeError(
    exception="NullCharacterError", base="ValueError", code=-4702
)
_BAD_PATTERN = NativeError(exception="error", module="re")


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    by_index = {t.index: t for t in node.terminals if t.direction == "input"}
    outputs = {t.index: t for t in node.terminals if t.direction == "output"}
    submatches = sorted(
        (
            t
            for t in outputs.values()
            if t.index not in (_BEFORE, _WHOLE, _AFTER, _OFFSET_PAST, _ERROR_OUT)
        ),
        key=lambda t: t.index,
    )
    if _STRING_IN not in by_index or _PATTERN_IN not in by_index:
        raise ConversionGap(f"{_NAME} has no input string or regular expression", node)

    def value(index: int, default: str) -> str:
        term = by_index.get(index)
        wired = ctx.wired_input(term.id, node) if term is not None else None
        return default if wired is None else wired

    text = value(_STRING_IN, "''")
    pattern = value(_PATTERN_IN, "''")
    offset = value(_OFFSET_IN, "0")
    ignore_case = value(_IGNORE_CASE_IN, "False")
    multiline = value(_MULTILINE_IN, "False")
    ctx.add_import("import re")

    match = ctx.make_output_var("match", node.id)
    string_var = ctx.make_output_var("input_string", node.id)
    names = {
        index: ctx.make_output_var(label, node.id, terminal_id=outputs[index].id)
        for index, label in (
            (_BEFORE, "before_match"),
            (_WHOLE, "whole_match"),
            (_AFTER, "after_match"),
            (_OFFSET_PAST, "offset_past_match"),
        )
        if index in outputs
    }
    group_names = {
        t.index: ctx.make_output_var("submatch", node.id, terminal_id=t.id)
        for t in submatches
    }
    # Always bound: the statements below read them whether or not wired.
    for index, label in (
        (_BEFORE, "before_match"),
        (_WHOLE, "whole_match"),
        (_AFTER, "after_match"),
        (_OFFSET_PAST, "offset_past_match"),
    ):
        if index not in names:
            names[index] = ctx.make_output_var(f"{label}_unused", node.id)

    compiled = _compiled(pattern, ignore_case, multiline, ctx)
    source = [
        f"{string_var} = {text}",
        f"if '\\x00' in {string_var} or '\\x00' in {pattern}:",
        "    raise NullCharacterError("
        "'Match Regular Expression: the strings contain a null character')",
        f"{match} = {compiled}.search({string_var}, max({offset}, 0))",
        f"if {match} is None:",
        f"    {names[_BEFORE]} = {string_var}",
        f"    {names[_WHOLE]} = ''",
        f"    {names[_AFTER]} = ''",
        f"    {names[_OFFSET_PAST]} = -1",
        *(f"    {name} = ''" for name in group_names.values()),
        "else:",
        f"    {names[_BEFORE]} = {string_var}[:{match}.start()]",
        f"    {names[_WHOLE]} = {match}.group(0)",
        f"    {names[_AFTER]} = {string_var}[{match}.end():]",
        f"    {names[_OFFSET_PAST]} = {match}.end()",
        *(
            f"    {name} = {match}.group({position}) "
            f"if {position} <= {match}.re.groups and "
            f"{match}.group({position}) is not None else ''"
            for position, name in enumerate(group_names.values(), start=1)
        ),
    ]
    statements = ast.parse("\n".join(source)).body
    bindings = {outputs[i].id: names[i] for i in names if i in outputs}
    bindings.update({t.id: group_names[t.index] for t in submatches})
    fragment = CodeFragment(statements=statements, bindings=bindings)
    return catch_failures(
        node,
        fragment,
        CatalogFailures((_NULL_CHARACTER, _BAD_PATTERN), {}, _NAME),
        ctx,
    )


def _compiled(
    pattern: str, ignore_case: str, multiline: str, ctx: CodeGenContext
) -> str:
    """The compiled pattern: a module-level ``re.compile`` when the pattern and
    both options are constants, so it is compiled once; otherwise compiled where
    the node runs."""
    flags = f"(re.I if {ignore_case} else 0) | (re.M if {multiline} else 0)"
    literal = _literal(pattern)
    constant_options = ignore_case in ("True", "False") and multiline in (
        "True",
        "False",
    )
    if literal is None or not constant_options or not _compiles(literal):
        # A pattern re cannot read is the node's error out when it runs, not an
        # error when the module is imported.
        return f"re.compile({pattern}, {flags})"
    digest = hashlib.sha1(  # noqa: S324 (a name, not a secret)
        f"{pattern}|{ignore_case}|{multiline}".encode()
    ).hexdigest()[:8]
    name = f"_regex_{digest}"
    ctx.add_module_global(name, parse_expr(f"re.compile({pattern}, {flags})"))
    return name


def _compiles(pattern: str) -> bool:
    if "\x00" in pattern:
        return False
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def _literal(expression: str) -> str | None:
    """The text of ``expression`` when it is a string literal, else None."""
    try:
        tree = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(tree, ast.Constant) and isinstance(tree.value, str):
        return tree.value
    return None

"""The vi.lib VI Search and Replace Pattern (``Utility/error.llb``).

NI, "Search and Replace Pattern VI" and its "(string)" instance
(labview-api-ref, ``vi-lib/utility/error-llb/search-and-replace-pattern-vi.html``
and ``...-string-vi.html``): searches ``string`` from ``offset`` for substrings
that match ``regular expression`` and replaces them with ``replace string``, the
first match only unless ``replace all?`` is TRUE. ``result string`` is the edited
string; ``offset past match`` "is the index in string of the first character after
the last match", -1 when nothing matches. The pattern is written in Match
Pattern's syntax ("This VI is similar to the Match Pattern function"; its
special-characters link goes to "Special Characters for Match Pattern"), so a
constant pattern is translated with ``codegen/match_pattern_regex.py`` when the
code is generated and a computed one by the function written into the module, as
Match Pattern does.

NI's unofficial copy of the page (https://rajsite.github.io/unofficial-lvdocs/glang/
Search_and_Replace_Pattern.html) adds: "If regular expression matches the empty
string, the function makes no replacements"; then ``result string`` is ``string``
and ``offset past match`` is 0 when ``replace all?`` is FALSE and the length of
``string`` when it is TRUE. When replace all finds an empty match after a
non-empty one, NI does not say what is replaced, so that call raises
``NotImplementedError`` when it runs. ``replace string`` is inserted as it is
(NI's page names no ``$n`` references). The text before ``offset`` is kept and the
search runs on ``string[offset:]``, so a leading ``^`` anchors at the offset, as
in Match Pattern.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import VINode
from lvkit.models import TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..match_pattern_regex import to_python_regex
from .ops._regex_node import (
    declare_translator,
    literal,
    search_start,
    string_variable,
)

_TRANSLATOR_NAME = "_match_pattern_regex"
AMBIGUOUS_EMPTY_MATCH = (
    "Search and Replace Pattern with replace all? found an empty match after a "
    "non-empty one: NI does not say what the VI replaces"
)


def _input(node: VINode, ctx: CodeGenContext, entry: VIEntry, param: str) -> str | None:
    """The expression feeding the catalog input ``param``; None when unwired."""
    index = next(
        t.index
        for t in entry.terminals
        if t.python_param == param and t.direction == TerminalDirection.INPUT
    )
    term = next(
        (
            t
            for t in node.terminals
            if t.index == index and t.direction == TerminalDirection.INPUT
        ),
        None,
    )
    return ctx.wired_input(term.id, node) if term is not None else None


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of the VI: the search, and the replacement of its matches."""
    statements: list[ast.stmt] = []
    string = string_variable(
        ctx, node, _input(node, ctx, entry, "string") or "''", statements
    )
    pattern = _input(node, ctx, entry, "regular_expression")
    constant = literal(pattern) if pattern is not None else ""
    if isinstance(constant, str):
        regex = repr(to_python_regex(constant))
    else:
        translate = declare_translator(ctx, _TRANSLATOR_NAME, to_python_regex)
        regex = f"{translate}({pattern})"
    replacement = _input(node, ctx, entry, "replace_string") or "''"
    replace_all = _input(node, ctx, entry, "replace_all") or "False"
    start = search_start(ctx, node, _input(node, ctx, entry, "offset"), statements)
    tail = string if start is None else f"{string}[{start}:]"
    head = "" if start is None else f"{string}[:{start}] + "
    past_base = "" if start is None else f"{start} + "

    names = {
        param: ctx.make_output_var(param, node.id)
        for param in ("result_string", "offset_past_match")
    }
    result, past = names["result_string"], names["offset_past_match"]
    source = (
        f"_tail = {tail}\n"
        f"_pattern = re.compile({regex})\n"
        f"_found = _pattern.search(_tail)\n"
        f"{result} = {string}\n"
        f"{past} = -1\n"
        f"if _found is not None:\n"
        f"    _matches = list(_pattern.finditer(_tail)) if {replace_all} "
        f"else [_found]\n"
        f"    if _matches[0].end() == _matches[0].start():\n"
        f"        {past} = len({string}) if {replace_all} else 0\n"
        f"    elif any(_m.end() == _m.start() for _m in _matches):\n"
        f"        raise NotImplementedError({AMBIGUOUS_EMPTY_MATCH!r})\n"
        f"    else:\n"
        f"        _pieces = []\n"
        f"        _cursor = 0\n"
        f"        for _m in _matches:\n"
        f"            _pieces += [_tail[_cursor:_m.start()], {replacement}]\n"
        f"            _cursor = _m.end()\n"
        f"        {result} = {head}''.join(_pieces) + _tail[_cursor:]\n"
        f"        {past} = {past_base}_matches[-1].end()\n"
    )
    statements.extend(ast.parse(source).body)
    bindings = {}
    for term in node.terminals:
        if term.direction != TerminalDirection.OUTPUT:
            continue
        for catalog in entry.terminals:
            if catalog.index == term.index and catalog.python_param in names:
                bindings[term.id] = names[catalog.python_param]
    return CodeFragment(statements=statements, bindings=bindings, imports={"import re"})

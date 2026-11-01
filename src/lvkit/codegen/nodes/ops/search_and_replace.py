"""Search and Replace String (primResID 3914).

Replaces the first or every match of ``search string`` in ``input string``, from
``offset``; the text before ``offset`` is kept. NI: "Search and Replace String
Function" (labview-api-ref, ``functions/search-and-replace-string.html``) and
"Special Characters for Match Regular Expression and Search and Replace String"
(labview, ``special-characters-for-match-regular-expression-and-search-and-
replace-string.html``).

* Plain mode: ``search string`` is the text itself (``re.escape``) and
  ``replace string`` is inserted as it is. A plain call that wants neither the
  count, the offset past the replacement, ignore case nor an offset is
  ``str.replace``.
* Regular Expression mode (the node's ``objFlags`` bit ``0x2000000``): ``search
  string`` is a PCRE pattern (``codegen/pcre_regex.py``) and ``replace string`` may
  hold ``$n`` / ``${n}`` submatch references and ``\\$``. A constant is translated
  when the code is generated; one computed at run time by a function written into
  the module.

The search runs on ``input string[start:]`` (as Match Pattern does) so a leading
``^`` anchors at ``offset``. ``offset past replacement`` is where the last
replacement ends in ``result string``, or -1 when nothing was replaced; it and
``number of replacements`` come from the matches.

Terminals (from the primitives entry):
  in  0 = input string, 1 = replace all?, 2 = ignore case?, 3 = multiline? (only
      in Regular Expression mode), 5 = search string, 7 = replace string,
      9 = offset
  out 4 = result string, 8 = number of replacements, 10 = offset past replacement
"""

from __future__ import annotations

import ast
import re

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...pcre_regex import pcre_to_python_regex, pcre_to_python_template
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

# The node's own ``objFlags`` bit that is set when the node is in Regular
# Expression mode (docs/_internal/design/labview-binary-format.md).
REGULAR_EXPRESSION_MODE = 0x2000000

_REGEX_TRANSLATOR = "_pcre_regex"
_TEMPLATE_TRANSLATOR = "_pcre_template"

_OUTPUTS = {
    4: "result_string",
    8: "number_of_replacements",
    10: "offset_past_replacement",
}


def _flag(ctx: CodeGenContext, node: PrimitiveNode, index: int) -> bool | str:
    """A Boolean input: its value when it is a constant (False when unwired),
    else the expression feeding it."""
    text = input_expression(ctx, node, index)
    if text is None:
        return False
    value = literal(text)
    return value if isinstance(value, bool) else text


def _gap(node: PrimitiveNode, what: str, why: object) -> ConversionGap:
    return ConversionGap(
        f"Search and Replace String's {what} cannot be read as Python re: {why}",
        node,
    )


def _constant_pattern(node: PrimitiveNode, pattern: str) -> str:
    try:
        translated = pcre_to_python_regex(pattern)
        re.compile(translated, re.ASCII)
    except (ValueError, re.error) as error:
        raise _gap(node, "regular expression", error) from error
    return translated


def _constant_template(node: PrimitiveNode, pattern: str, replacement: str) -> str:
    translated = pcre_to_python_template(replacement)
    try:
        compiled = re.compile(_constant_pattern(node, pattern), re.ASCII)
        compiled.sub(translated, "")
        for reference in re.findall(r"\\g<(\d+)>", translated):
            if int(reference) > compiled.groups:
                raise ValueError(f"the submatch {reference} does not exist")
    except (ValueError, re.error) as error:
        raise _gap(node, "replace string", error) from error
    return translated


@register_op_fragment("SEARCH_AND_REPLACE_STRING")
def search_and_replace(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    wired = [index for index in _OUTPUTS if is_wired_output(ctx, node, index)]
    if not wired:
        return CodeFragment.empty()
    regex_mode = bool(node.obj_flags & REGULAR_EXPRESSION_MODE)
    replace_all = _flag(ctx, node, 1)
    ignore_case = _flag(ctx, node, 2)
    multiline = _flag(ctx, node, 3) if regex_mode else False
    search = input_expression(ctx, node, 5)
    replace = input_expression(ctx, node, 7)
    offset = input_expression(ctx, node, 9)
    offset_value = literal(offset) if offset is not None else 0
    starts_at_zero = isinstance(offset_value, int) and offset_value <= 0

    statements: list[ast.stmt] = []
    string = string_variable(
        ctx, node, input_expression(ctx, node, 0) or "''", statements
    )
    outputs = {
        index: terminal(node, "output", index)
        for index in wired
        if terminal(node, "output", index) is not None
    }
    bindings: dict[str, str] = {}

    def output_variable(index: int) -> str:
        term = outputs[index]
        assert term is not None
        name = ctx.make_output_var(_OUTPUTS[index], node.id, terminal_id=term.id)
        bindings[term.id] = name
        return name

    if not regex_mode and ignore_case is False and starts_at_zero and wired == [4]:
        count = (
            ""
            if replace_all is True
            else ", 1"
            if replace_all is False
            else f", -1 if {replace_all} else 1"
        )
        empty = "''"
        text = f"{string}.replace({search or empty}, {replace or empty}{count})"
        statements.append(build_assign(output_variable(4), parse_expr(text)))
        return CodeFragment(statements=statements, bindings=bindings)

    start = search_start(ctx, node, offset, statements)
    rest = string if start is None else f"{string}[{start}:]"

    # The pattern, the replacement and the flags.
    flags = []
    if regex_mode or ignore_case is not False:
        flags.append("re.ASCII")
    for flag, name in ((ignore_case, "IGNORECASE"), (multiline, "MULTILINE")):
        if flag is True:
            flags.append(f"re.{name}")
        elif flag is not False:
            flags.append(f"(re.{name} if {flag} else 0)")
    flags_argument = f", flags={' | '.join(flags)}" if flags else ""
    search_text = search or "''"
    replace_text = replace or "''"
    search_value = literal(search_text)
    replace_value = literal(replace_text)
    if regex_mode:
        if isinstance(search_value, str) and isinstance(replace_value, str):
            pattern = repr(_constant_pattern(node, search_value))
            replacement = repr(_constant_template(node, search_value, replace_value))
        else:
            if isinstance(search_value, str):
                pattern = repr(_constant_pattern(node, search_value))
            else:
                regex = declare_translator(ctx, _REGEX_TRANSLATOR, pcre_to_python_regex)
                pattern = f"{regex}({search_text})"
            if isinstance(replace_value, str):
                template = repr(pcre_to_python_template(replace_value))
            else:
                translate = declare_translator(
                    ctx, _TEMPLATE_TRANSLATOR, pcre_to_python_template
                )
                template = f"{translate}({replace_text})"
            replacement = template
    else:
        pattern = (
            repr(re.escape(search_value))
            if isinstance(search_value, str)
            else f"re.escape({search_text})"
        )
        replacement = ""
    if replace_all is True:
        limit = ""
    elif replace_all is False:
        limit = ", count=1"
    else:
        limit = f", count=0 if {replace_all} else 1"

    head = "" if start is None else f"{string}[:{start}] + "
    if 10 not in wired:
        keeps_head = start is not None
        if not regex_mode:
            parameter = ctx.make_output_var("match", node.id)
            if parameter == replace_text:
                parameter = ctx.make_output_var("matched", node.id)
            replacement = f"lambda {parameter}: {replace_text}"
        replaced = (
            output_variable(4)
            if 4 in wired and not keeps_head
            else ctx.make_output_var("replaced", node.id)
        )
        number = output_variable(8) if 8 in wired else None
        function = "subn" if number else "sub"
        call = f"re.{function}({pattern}, {replacement}, {rest}{limit}{flags_argument})"
        target = f"{replaced}, {number}" if number else replaced
        statements.extend(ast.parse(f"{target} = {call}").body)
        if 4 in wired and keeps_head:
            statements.append(
                build_assign(output_variable(4), parse_expr(f"{head}{replaced}"))
            )
        return CodeFragment(
            statements=statements, bindings=bindings, imports={"import re"}
        )

    # The offset past the last replacement needs where each match was.
    if start is not None:
        remainder = ctx.make_output_var("remainder", node.id)
        statements.append(build_assign(remainder, parse_expr(rest)))
        rest = remainder
    matches = ctx.make_output_var("matches", node.id)
    pieces = ctx.make_output_var("pieces", node.id)
    cursor = ctx.make_output_var("cursor", node.id)
    match = ctx.make_output_var("match", node.id)
    replaced = ctx.make_output_var("replaced", node.id)
    if replace_all is True:
        keep = ""
    elif replace_all is False:
        keep = "[:1]"
    else:
        keep = f"[:None if {replace_all} else 1]"
    inserted = f"{match}.expand({replacement})" if regex_mode else replace_text
    statements.extend(
        ast.parse(
            f"{matches} = list(re.finditer({pattern}, {rest}{flags_argument})){keep}\n"
            f"{pieces} = []\n"
            f"{cursor} = 0\n"
            f"for {match} in {matches}:\n"
            f"    {pieces}.append({rest}[{cursor}:{match}.start()])\n"
            f"    {pieces}.append({inserted})\n"
            f"    {cursor} = {match}.end()\n"
            f"{replaced} = ''.join({pieces})"
        ).body
    )
    if 4 in wired:
        statements.append(
            build_assign(
                output_variable(4), parse_expr(f"{head}{replaced} + {rest}[{cursor}:]")
            )
        )
    if 8 in wired:
        statements.append(
            build_assign(output_variable(8), parse_expr(f"len({matches})"))
        )
    past = f"len({replaced})" if start is None else f"{start} + len({replaced})"
    statements.append(
        build_assign(output_variable(10), parse_expr(f"{past} if {matches} else -1"))
    )
    return CodeFragment(statements=statements, bindings=bindings, imports={"import re"})

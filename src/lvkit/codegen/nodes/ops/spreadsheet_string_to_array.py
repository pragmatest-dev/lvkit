"""Spreadsheet String To Array (prim 1539).

The output array's DIMENSIONALITY and element type — not a wire — decide how the
string is split: a 2-D array type splits rows on the EOL and columns on the
delimiter, while a 1-D array type treats the delimiter and an EOL alike as
element separators. Those come from the output terminal's type, so this is
emitted as a logic op rather than a static template. Per-field conversion
(str/int/float) follows the element type; a field that is empty or not a number
reads as zero, through a small function written into the module.

Terminals (from the JSON entry):
  in_1 = delimiter
  in_3 = spreadsheet string
  out index 0 = array
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal

# The module-level function that reads one field as a number.
_PARSE = """def {name}(field):
    try:
        return {kind}(field)
    except ValueError:
        return {zero}
"""
_KINDS = {"int": ("int", "0"), "float": ("float", "0.0")}


def _element_kind(array_type: LVType | None) -> str:
    """Map the array's element type to the kind of conversion it needs."""
    elem = getattr(array_type, "element_type", None)
    underlying = getattr(elem, "underlying_type", None) or ""
    if underlying.startswith(("NumFloat", "NumComplex")):
        return "float"
    if underlying.startswith(("NumInt", "NumUInt")):
        return "int"
    return "str"


def _number_parser(ctx: CodeGenContext, kind: str) -> str:
    """The name of the module's function that reads a field as ``kind``."""
    key = ("spreadsheet", kind)
    if key not in ctx.flat_functions:
        name = f"_parse_{kind}_field"
        ctx.flat_functions[key] = name
        function, zero = _KINDS[kind]
        source = _PARSE.format(name=name, kind=function, zero=zero)
        ctx.formula_helpers.append(ast.parse(source).body[0])
    return ctx.flat_functions[key]


@register_op_fragment("SPREADSHEET_STRING_TO_ARRAY")
def spreadsheet_string_to_array(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    result = terminal(node, "output", 0)
    if result is None or not ctx.is_wired(result.id):
        return CodeFragment.empty()
    delimiter_term = terminal(node, "input", 1)
    text_term = terminal(node, "input", 3)
    delimiter = (ctx.resolve(delimiter_term.id) if delimiter_term else None) or "''"
    text = (ctx.resolve(text_term.id) if text_term else None) or "''"
    out_type = result.lv_type
    ndims = getattr(out_type, "dimensions", None) or 1
    kind = _element_kind(out_type)
    separator = f"({delimiter} or '\\t')"
    parse = _number_parser(ctx, kind) if kind != "str" else None
    if ndims >= 2:
        row = f"_row.split({separator})"
        if parse:
            row = f"[{parse}(_cell) for _cell in {row}]"
        expression = f"[{row} for _row in {text}.splitlines()]"
    else:
        # One dimension: the delimiter and an end of line both separate elements
        # (CR LF first, so it counts as one separator).
        pattern = f"'\\r\\n|\\r|\\n|' + re.escape({separator})"
        fields = f"re.split({pattern}, {text})"
        if parse:
            fields = f"[{parse}(_field) for _field in {fields}]"
        expression = f"{fields} if {text} else []"
    variable = ctx.make_output_var("array", node.id, terminal_id=result.id)
    statements: list[ast.stmt] = [build_assign(variable, parse_expr(expression))]
    ctx.add_import("import re")
    return CodeFragment(statements=statements, bindings={result.id: variable})

"""Code generator for Format String (printf) nodes."""

from __future__ import annotations

import ast
import re

from lvkit.graph.models import PrimitiveNode

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..output_binding import bind_output


def _is_runtime_value(expr: str) -> bool:
    """Whether the resolved expression is a variable, attribute or call -- a value
    known only at run time -- rather than a literal written on the diagram."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False
    return not isinstance(tree.body, ast.Constant)


_PLACEHOLDER = re.compile(r"%[sdfeEgGoxXcr]")


def _literal_text(source: str) -> str:
    """The text of the string literal ``source`` (its escapes decoded), or the
    source itself when it is not a literal."""
    try:
        value = ast.literal_eval(source)
    except (ValueError, SyntaxError):
        return source
    return value if isinstance(value, str) else str(value)


def _format_text(text: str, args: list[str]) -> ast.JoinedStr:
    """An f-string with each placeholder of ``text`` replaced by the next of
    ``args`` (a placeholder with no argument stays as text). The pieces are
    nodes, so any character of the text is written exactly."""
    values: list[ast.expr] = []
    literal = ""
    position = 0
    used = 0
    for match in _PLACEHOLDER.finditer(text):
        if used == len(args):
            break
        literal += text[position : match.start()]
        if literal:
            values.append(ast.Constant(value=literal))
        literal = ""
        values.append(ast.FormattedValue(value=parse_expr(args[used]), conversion=-1))
        used += 1
        position = match.end()
    literal += text[position:]
    if literal:
        values.append(ast.Constant(value=literal))
    return ast.JoinedStr(values=values)


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a printf (Format String) node."""
    inputs = sorted(
        [t for t in node.terminals if t.direction == "input"],
        key=lambda t: t.index,
    )
    outputs = [t for t in node.terminals if t.direction == "output"]

    if not outputs:
        return CodeFragment.empty()

    # Resolve wired non-error input values.
    input_values = []
    for t in inputs:
        if not ctx.is_wired(t.id):
            continue
        if t.is_error_cluster:
            continue
        val = ctx.wired_input(t.id, node) or "None"
        input_values.append(val)

    # First input is format string, rest are arguments.
    expr: ast.expr
    if len(input_values) >= 2 and _is_runtime_value(input_values[0]):
        # A format string that is a wired value is applied at run time; Python's
        # ``%`` takes the same ``%s``/``%d``/``%f``/... directives.
        args_tuple = ", ".join(input_values[1:])
        expr = parse_expr(f"{input_values[0]} % ({args_tuple},)")
    elif len(input_values) >= 2:
        expr = _format_text(_literal_text(input_values[0]), input_values[1:])
    elif len(input_values) == 1:
        expr = parse_expr(f"str({input_values[0]})")
    else:
        expr = ast.Constant(value="")

    out_term = outputs[0]
    base_name = out_term.name or "formatted"
    return bind_output(node, out_term.id, base_name, expr, ctx)

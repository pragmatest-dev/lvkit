"""Code generator for Format String (printf) nodes."""

from __future__ import annotations

import ast
import re

from lvkit.graph.models import PrimitiveNode

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment


def _is_runtime_value(expr: str) -> bool:
    """Whether the resolved expression is a variable, attribute or call -- a value
    known only at run time -- rather than a literal written on the diagram."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False
    return not isinstance(tree.body, ast.Constant)


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
    if len(input_values) >= 2 and _is_runtime_value(input_values[0]):
        # A format string that is a wired value is applied at run time; Python's
        # ``%`` takes the same ``%s``/``%d``/``%f``/... directives.
        args_tuple = ", ".join(input_values[1:])
        expr_str = f"{input_values[0]} % ({args_tuple},)"
    elif len(input_values) >= 2:
        fmt_str = input_values[0].strip("'\"")
        args = input_values[1:]
        arg_idx = 0

        def _replace_placeholder(m: re.Match) -> str:
            nonlocal arg_idx
            if arg_idx < len(args):
                replacement = f"{{{args[arg_idx]}}}"
                arg_idx += 1
                return replacement
            return m.group()

        result = re.sub(r"%[sdfeEgGoxXcr]", _replace_placeholder, fmt_str)
        # Unescape LabVIEW backslash-quoted characters that survive strip().
        result = re.sub(r"""\\(['"])""", r"\1", result)
        # A trailing bare backslash would break the triple-quote delimiter.
        if result.endswith("\\"):
            result = result[:-1]
        expr_str = f'f"""{result}"""'
    elif len(input_values) == 1:
        expr_str = f"str({input_values[0]})"
    else:
        expr_str = "''"

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}

    out_term = outputs[0]
    var_name = to_var_name(out_term.name or "formatted")
    statements.append(build_assign(var_name, parse_expr(expr_str)))
    bindings[out_term.id] = var_name

    return CodeFragment(statements=statements, bindings=bindings)

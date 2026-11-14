"""Replace Substring (primResID 3913).

NI, "Replace Substring Function" (labview-api-ref,
``functions/replace-substring.html``): "Inserts, deletes, or replaces a substring
at the offset you specify in string." The function "deletes length bytes in string
starting at offset, and replaces the deleted portion with substring. If length is
0, the Replace Substring function inserts substring at offset. If substring is
empty, the Replace Substring function deletes length bytes at offset."

* ``substring`` defaults to the empty string, ``offset`` to 0 and ``length`` to the
  length of ``substring``.
* ``replaced substring`` "contains the bytes that were replaced in string";
  ``result string`` "contains the edited string with the replaced bytes".
* NI does not describe an offset below 0 or past the end of the string, or a
  negative length: as for String Subset they are cut to the string (an offset
  below 0 is 0, one past the end appends, a negative length is 0).

Terminals (from the primitives entry): in 2 = length, 3 = offset, 4 = substring,
5 = string; out 0 = replaced substring, 1 = result string.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable


@register_op_fragment("REPLACE_SUBSTRING")
def replace_substring(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    string = string_variable(
        ctx, node, input_expression(ctx, node, 5) or "''", statements, "string"
    )
    substring = string_variable(
        ctx, node, input_expression(ctx, node, 4) or "''", statements, "substring"
    )
    offset = input_expression(ctx, node, 3)
    length = input_expression(ctx, node, 2)
    start = ctx.make_output_var("start", node.id)
    statements.append(
        build_assign(start, parse_expr("0" if offset is None else f"max({offset}, 0)"))
    )
    end = ctx.make_output_var("end", node.id)
    width = f"len({substring})" if length is None else f"max({length}, 0)"
    statements.append(build_assign(end, parse_expr(f"{start} + {width}")))
    bindings: dict[str, str] = {}
    replaced = terminal(node, "output", 0)
    if replaced is not None and ctx.is_wired(replaced.id):
        name = ctx.make_output_var(
            "replaced_substring", node.id, terminal_id=replaced.id
        )
        statements.append(build_assign(name, parse_expr(f"{string}[{start}:{end}]")))
        bindings[replaced.id] = name
    result = terminal(node, "output", 1)
    if result is not None and ctx.is_wired(result.id):
        name = ctx.make_output_var("result_string", node.id, terminal_id=result.id)
        statements.append(
            build_assign(
                name, parse_expr(f"{string}[:{start}] + {substring} + {string}[{end}:]")
            )
        )
        bindings[result.id] = name
    return CodeFragment(statements=statements, bindings=bindings)

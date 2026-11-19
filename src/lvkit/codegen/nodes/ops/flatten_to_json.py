"""Flatten To JSON (primResID 24201).

Converts ``anything``, an array or cluster of Booleans, numbers and strings, to
a JSON string (NI, Flatten To JSON Function). The value is written as dicts and
lists from its type when the code is generated (``codegen/plain_values.py``:
a cluster with labels is an object keyed by them, an unlabelled one an object
keyed "0", "1", ...), and ``json.dumps`` writes it without spaces, as NI's
example ``{"0":"abc","1":false,"2":[9,8,7]}`` shows. NaN and infinity are
written as ``NaN``, ``Infinity`` and ``-Infinity``, the tokens LabVIEW's
extensions use; with ``enable LabVIEW extensions?`` FALSE they fail as errors
-375010 and -375011.

A number's text is Python's shortest form that reads back as the same double
(LabVIEW writes up to 20 significant digits, so 0.1 is 0.10000000000000000555
there); any JSON parser reads both as the same number.

Terminals (primitives entry):
  in  index  0 = anything
  in  index  3 = error in
  in  index  6 = enable LabVIEW extensions? (T)
  out index  8 = JSON string
  out index 11 = error out
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...plain_values import UnsupportedPlainType, is_container, to_plain
from . import register_op_fragment
from ._flat_node import terminal
from ._json_node import boolean_input, parse_constant_argument


@register_op_fragment("FLATTEN_TO_JSON")
def flatten_to_json(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    out_term = terminal(node, "output", 8)
    if out_term is None or not ctx.is_wired(out_term.id):
        return CodeFragment.empty()
    source = terminal(node, "input", 0)
    lv_type = source.lv_type if source else None
    try:
        if lv_type is None or not is_container(lv_type):
            raise UnsupportedPlainType(
                "a value that is not an array or a cluster"
                if lv_type is not None
                else "an unwired value"
            )
        text = ctx.wired_input(source.id, node) if source else None
        value = ctx.make_output_var("anything", node.id)
        plain = to_plain(value, lv_type, ctx)
    except UnsupportedPlainType as error:
        raise ConversionGap(
            f"Flatten To JSON does not handle {error}: NI accepts arrays and "
            "clusters of Booleans, numbers and strings",
            node,
        ) from error
    variable = ctx.make_output_var("json_string", node.id, terminal_id=out_term.id)
    statements: list[ast.stmt] = [
        build_assign(value, parse_expr(text or "None")),
        build_assign(
            variable,
            parse_expr(
                f"json.dumps({plain}, separators=(',', ':'), "
                "ensure_ascii=False, allow_nan=True)"
            ),
        ),
    ]
    extensions = boolean_input(ctx, node, 6, True)
    definition, argument = parse_constant_argument(ctx, node, extensions)
    if argument:
        statements.extend(definition)
        statements.append(
            ast.Expr(value=parse_expr(f"json.loads({variable}{argument})"))
        )
    ctx.add_import("import json")
    return CodeFragment(statements=statements, bindings={out_term.id: variable})

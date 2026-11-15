"""Unflatten From JSON (primResID 24202).

Converts a JSON string to the type wired to ``type and defaults`` (NI, Unflatten
From JSON Function). ``json.loads`` reads the string; ``path`` is followed as a
loop over its strings (each the name of a cluster element or an integer index of
an array); the value is built from the type written out when the code is
generated (``codegen/plain_values.py``), a cluster element by its label, or by
"0", "1", ... when the cluster has no labels.

Failures are LabVIEW's JSON errors (``_json_node``): invalid JSON -375003, a
path that is not there -375004, a JSON value of another type -375005, a cluster
element missing from the JSON -375006, an extra item with ``strict
validation?`` -375007, NaN or an infinity with ``enable LabVIEW extensions?``
FALSE -375010 and -375011, an integer out of range -375012. A null with
``default null elements?`` FALSE is an error NI gives no code for, so it is the
generic one.

Terminals (primitives entry):
  in  index  0 = JSON string
  in  index  1 = type and defaults
  in  index  3 = error in
  in  index  4 = path (array of strings)
  in  index  5 = default null elements? (F)
  in  index  6 = enable LabVIEW extensions? (T)
  in  index  7 = strict validation? (F)
  out index  8 = value
  out index 11 = error out
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...plain_values import PlainOptions, UnsupportedPlainType, from_plain
from . import register_op_fragment
from ._flat_node import terminal
from ._json_node import (
    ERRORS,
    PATH_NOT_FOUND,
    ROOT,
    boolean_input,
    parse_constant_argument,
)

_PATH_STEP = """\
for {step} in {path}:
    if isinstance({document}, dict) and {step} in {document}:
        {document} = {document}[{step}]
    elif (
        isinstance({document}, list)
        and {step}.isdecimal()
        and int({step}) < len({document})
    ):
        {document} = {document}[int({step})]
    else:
        raise {error}('the path element ' + repr({step}) + ' is not in the JSON')
"""


@register_op_fragment("UNFLATTEN_FROM_JSON")
def unflatten_from_json(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    value_term = terminal(node, "output", 8)
    if value_term is None or not ctx.is_wired(value_term.id):
        return CodeFragment.empty()
    type_term = terminal(node, "input", 1)
    lv_type = (type_term.lv_type if type_term else None) or value_term.lv_type
    if lv_type is None or type_term is None:
        raise ConversionGap("Unflatten From JSON needs a type and defaults", node)
    text_term = terminal(node, "input", 0)
    text = (ctx.wired_input(text_term.id, node) if text_term else None) or "''"
    extensions = boolean_input(ctx, node, 6, True)
    definition, argument = parse_constant_argument(ctx, node, extensions)
    document = ctx.make_output_var("document", node.id)
    statements = [
        *definition,
        build_assign(document, parse_expr(f"json.loads({text}{argument})")),
    ]
    path_term = terminal(node, "input", 4)
    path = ctx.wired_input(path_term.id, node) if path_term else None
    if path is None:
        statements.extend(
            _parse(
                f"if not isinstance({document}, (dict, list)):\n"
                f"    raise {ROOT}('the JSON root must be an array or a cluster')\n"
            )
        )
    else:
        step = ctx.make_output_var("step", node.id)
        statements.extend(
            _parse(
                _PATH_STEP.format(
                    step=step, path=path, document=document, error=PATH_NOT_FOUND
                )
            )
        )
    default = ctx.make_output_var("defaults", node.id)
    statements.append(
        build_assign(default, parse_expr(ctx.wired_input(type_term.id, node) or "None"))
    )
    variable = ctx.make_output_var("value", node.id, terminal_id=value_term.id)
    options = PlainOptions(
        null_to_default=boolean_input(ctx, node, 5, False),
        strict=boolean_input(ctx, node, 7, False),
        errors=ERRORS,
    )
    try:
        statements.extend(
            from_plain(document, lv_type, default, variable, ctx, node.id, options)
        )
    except UnsupportedPlainType as error:
        raise ConversionGap(
            f"Unflatten From JSON does not handle {error}: NI accepts Booleans, "
            "integers, floating-point numbers, strings and arrays or clusters "
            "of them",
            node,
        ) from error
    ctx.add_import("import json")
    return CodeFragment(statements=statements, bindings={value_term.id: variable})


def _parse(source: str) -> list[ast.stmt]:
    return ast.parse(source).body

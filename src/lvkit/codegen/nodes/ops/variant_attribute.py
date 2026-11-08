"""Get Variant Attribute (primResID 8205) and Delete Variant Attribute (8206) on
an empty variant given attributes: a lookup table, a ``dict`` (``_variant_lookup``).

NI, "Get Variant Attribute Function": with ``name`` wired it returns the value of
that attribute, ``found`` is TRUE when the attribute exists, and an attribute
that is not found gives ``default value``; with ``name`` unwired it returns
``names`` (a 1D array, "sorted ... in ascending order") and ``values`` (the value
of each, in the order of ``names``). ``duplicate variant`` is the variant input.
An unwired ``default value`` is an empty variant, ``None``.

NI, "Delete Variant Attribute Function": "If name matches an attribute, this
function deletes the attribute and its value. If name is an empty string, this
function deletes all the attributes". ``found`` is TRUE when the attribute was
found; unwired ``name``: "TRUE if the function located and deleted attributes".

Get terminals: in 4 = error in, 5 = default value, 6 = name, 7 = variant; out 0 =
error out, 1 = value (values), 2 = found (names), 3 = duplicate variant. Delete
terminals: in 4 = error in, 6 = name, 7 = variant; out 0 = error out, 2 = found, 3
= variant out.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable
from ._variant_lookup import attribute_table


def _output(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    index: int,
    base: str,
    bindings: dict[str, str],
) -> str:
    """A name for output ``index``, bound to its terminal when the node has it."""
    term = terminal(node, "output", index)
    if term is None:
        return ctx.make_output_var(base, node.id)
    var = ctx.make_output_var(base, node.id, terminal_id=term.id)
    bindings[term.id] = var
    return var


@register_op_fragment("GET_VARIANT_ATTRIBUTE")
def get_variant_attribute(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    table, variant = attribute_table(ctx, node, "Get Variant Attribute", statements)
    bindings: dict[str, str] = {}
    name_text = input_expression(ctx, node, 6)
    value = _output(ctx, node, 1, "attribute_value", bindings)
    found = _output(ctx, node, 2, "found", bindings)
    duplicate = _output(ctx, node, 3, "duplicate_variant", bindings)
    if name_text is None:
        source = (
            f"{found} = sorted({table})\n"
            f"{value} = [{table}[key] for key in {found}]\n"
        )
    else:
        name = string_variable(ctx, node, name_text, statements, "name")
        default = input_expression(ctx, node, 5) or "None"
        source = (
            f"{value} = {table}.get({name}, {default})\n{found} = {name} in {table}\n"
        )
    statements.extend(ast.parse(source + f"{duplicate} = {variant}").body)
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("DELETE_VARIANT_ATTRIBUTE")
def delete_variant_attribute(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    table, _ = attribute_table(ctx, node, "Delete Variant Attribute", statements)
    bindings: dict[str, str] = {}
    name = string_variable(
        ctx, node, input_expression(ctx, node, 6) or "''", statements, "name"
    )
    found = _output(ctx, node, 2, "found", bindings)
    variant_out = _output(ctx, node, 3, "variant_out", bindings)
    source = (
        f"{found} = {name} in {table} if {name} != '' else len({table}) > 0\n"
        f"{variant_out} = {{key: item for key, item in {table}.items() "
        f"if {name} != '' and key != {name}}}"
    )
    statements.extend(ast.parse(source).body)
    return CodeFragment(statements=statements, bindings=bindings)

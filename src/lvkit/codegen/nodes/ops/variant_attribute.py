"""Get Variant Attribute (primResID 8205) and Delete Variant Attribute (8206) on
a ``Variant`` (``variants_module``): its attributes are the ``attributes``
dict of the variant.

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
from ...variant_values import is_variant_type, variant_input
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable
from ._variant_input import owned_variant, read_variant


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
    variant = read_variant(ctx, node, statements)
    bindings: dict[str, str] = {}
    name_text = input_expression(ctx, node, 6)
    value = _output(ctx, node, 1, "attribute_value", bindings)
    found = _output(ctx, node, 2, "found", bindings)
    duplicate = _output(ctx, node, 3, "duplicate_variant", bindings)
    if name_text is None:
        source = (
            f"{found} = sorted({variant}.attributes)\n"
            f"{value} = [{variant}.attributes[key] for key in {found}]\n"
        )
    else:
        name = string_variable(ctx, node, name_text, statements, "name")
        default = variant_input(ctx, node, terminal(node, "input", 5))
        read = f"{variant}.attributes.get({name}, {default})"
        out = terminal(node, "output", 1)
        if out is not None and out.lv_type is not None:
            if not is_variant_type(out.lv_type):
                read += ".value"
        source = f"{value} = {read}\n{found} = {name} in {variant}.attributes\n"
    statements.extend(ast.parse(source + f"{duplicate} = {variant}").body)
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("DELETE_VARIANT_ATTRIBUTE")
def delete_variant_attribute(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    statements: list[ast.stmt] = []
    variant = owned_variant(ctx, node, statements)
    bindings: dict[str, str] = {}
    name = string_variable(
        ctx, node, input_expression(ctx, node, 6) or "''", statements, "name"
    )
    found = _output(ctx, node, 2, "found", bindings)
    variant_out = _output(ctx, node, 3, "variant_out", bindings)
    source = (
        f"if {name} != '':\n"
        f"    {found} = {name} in {variant}.attributes\n"
        f"    {variant}.attributes.pop({name}, None)\n"
        "else:\n"
        f"    {found} = len({variant}.attributes) > 0\n"
        f"    {variant}.attributes.clear()\n"
        f"{variant_out} = {variant}"
    )
    statements.extend(ast.parse(source).body)
    return CodeFragment(statements=statements, bindings=bindings)

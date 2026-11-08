"""Set Variant Attribute (primResID 8204).

NI, "Set Variant Attribute Function" (labview-api-ref,
``functions/set-variant-attribute.html``): "Allows you to change or create an
attribute and value for variant data. ... If name matches an attribute, this
function replaces the attribute with the value specified. If name does not match
an attribute, this function creates a new attribute. If name is an empty string,
this function returns error code 1 because an empty string triggers a special
behavior in the Delete Variant Attribute function." ``replaced`` "is TRUE if an
attribute and value were replaced"; ``variant out`` is "the variant data with new
attributes".

A variant is the Python value itself and an attribute is an attribute of that
value (``Get Variant Attribute``: ``getattr(variant, name, default)``,
``hasattr``), so the variant out is a copy of the variant (a variant is a value,
not shared) with the attribute set. A value that cannot carry attributes (None,
a number, a string, a tuple) raises ``AttributeError``; an empty variant (an
unwired variant, or a constant: ``None``, or the text a ``Variant()`` constant is
written as) has no object to carry the attribute, so that is a gap. The empty
name is error 1, raised as the class of the node's own the catalog entry names
(``base``).

Terminals (from the primitives entry): in 4 = error in, 5 = value, 6 = name, 7 =
variant; out 0 = error out, 2 = replaced, 3 = variant out.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, string_variable

# NI, General LabVIEW Error Codes, 1: "An input parameter is invalid."
EMPTY_NAME = "An input parameter is invalid: the attribute name is empty."


def _is_literal(text: str) -> bool:
    """Whether the expression ``text`` is a literal (the empty variant constant
    is ``None`` or a string): a value that cannot carry an attribute."""
    try:
        ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return False
    return True


@register_op_fragment("SET_VARIANT_ATTRIBUTE")
def set_variant_attribute(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    variant_text = input_expression(ctx, node, 7)
    if variant_text is None or _is_literal(variant_text):
        raise ConversionGap(
            "Set Variant Attribute on an empty variant: a variant is the Python "
            "value itself, and a constant has no object to carry an attribute",
            node,
        )
    value = input_expression(ctx, node, 5) or "None"
    statements: list[ast.stmt] = []
    variant = string_variable(ctx, node, variant_text, statements, "variant")
    name = string_variable(
        ctx, node, input_expression(ctx, node, 6) or "''", statements, "name"
    )
    value = string_variable(ctx, node, value, statements, "value")
    ctx.add_import("import copy")
    out = terminal(node, "output", 3)
    replaced = terminal(node, "output", 2)
    variant_out = (
        ctx.make_output_var("variant_out", node.id, terminal_id=out.id)
        if out is not None
        else ctx.make_output_var("variant_out", node.id)
    )
    replaced_var = (
        ctx.make_output_var("replaced", node.id, terminal_id=replaced.id)
        if replaced is not None
        else ctx.make_output_var("replaced", node.id)
    )
    source = (
        f"if {name} == '':\n"
        f"    raise EmptyAttributeNameError({EMPTY_NAME!r})\n"
        f"{variant_out} = copy.copy({variant})\n"
        f"{replaced_var} = hasattr({variant_out}, {name})\n"
        f"setattr({variant_out}, {name}, {value})"
    )
    statements.extend(ast.parse(source).body)
    bindings: dict[str, str] = {}
    if out is not None:
        bindings[out.id] = variant_out
    if replaced is not None:
        bindings[replaced.id] = replaced_var
    return CodeFragment(statements=statements, bindings=bindings)

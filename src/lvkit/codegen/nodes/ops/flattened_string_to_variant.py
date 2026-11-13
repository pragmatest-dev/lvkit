"""Flattened String To Variant (primResID 8202).

A variant is a ``Variant`` (``variants_module``). When ``type string`` is known as
the code is generated (a diagram constant, or the ``type string`` of a Variant To
Flattened String whose variant has a known type) the descriptor is decoded then
(``type_string.py``) and ``data string`` is read with the ``struct`` calls for
that type (``codegen/flat.py``), in LabVIEW's byte format: the same code Unflatten
From String writes. A type that cannot be read that way is a gap.

A ``type string`` computed as the program runs names no type the code can be
written for. When ``data string`` is what a Variant To Flattened String pickled
(its variant's type unknown when generating) it is unpickled. (Unpickling runs
code the string names, so the string must come from a trusted source.) Any other
data string holds LabVIEW's flattened bytes, which only a known type reads: that
is a gap.

Terminals (from the primitives entry):
  in  index 4 = error in
  in  index 6 = data string
  in  index 7 = type string (array of I16)
  out index 0 = error out
  out index 3 = Variant
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.type_string import TypeStringError

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import UnsupportedLayout, layout_of
from ...fragment import CodeFragment
from ...variant_values import type_expression
from ...variants_module import VARIANT_NAME
from . import register_op_fragment
from ._flat_node import (
    add_flat_imports,
    read_flattened,
    terminal,
    unsupported_layout_error,
)
from ._variant_flat import is_pickled, type_string_type


@register_op_fragment("FLATTENED_STRING_TO_VARIANT")
def flattened_string_to_variant(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    variant_term = terminal(node, "output", 3)
    if variant_term is None or not ctx.is_wired(variant_term.id):
        return CodeFragment.empty()
    string_term = terminal(node, "input", 6)
    text = (ctx.wired_input(string_term.id, node) if string_term else None) or "''"
    try:
        lv_type = type_string_type(ctx, node, terminal(node, "input", 7))
    except TypeStringError as error:
        raise ConversionGap(
            f"Flattened String To Variant cannot read its type string: {error}", node
        ) from error
    if lv_type is None:
        if not is_pickled(ctx, string_term):
            raise ConversionGap(
                "Flattened String To Variant's type is known only when the code "
                "runs, and its data string holds LabVIEW's flattened bytes, not a "
                "pickle: the generated code has no LabVIEW type to read them with",
                node,
            )
        ctx.add_import("import pickle")
        variable = ctx.make_output_var("variant", node.id, terminal_id=variant_term.id)
        return CodeFragment(
            statements=[
                build_assign(
                    variable, parse_expr(f"pickle.loads({text}.encode('latin-1'))")
                )
            ],
            bindings={variant_term.id: variable},
        )
    try:
        layout = layout_of(lv_type)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    statements, variable, _ = read_flattened(
        ctx, node, layout, text, ">", True, variant_term, True
    )
    assert variable is not None
    ctx.import_variant()
    statements.append(
        build_assign(
            variable,
            parse_expr(f"{VARIANT_NAME}({variable}, {type_expression(ctx, lv_type)})"),
        )
    )
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings={variant_term.id: variable})

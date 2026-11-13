"""Variant To Flattened String (primResID 8203).

A variant is a ``Variant`` (``variants_module``). When the variant comes from To
Variant, the type is that of the wire fed to it, known when the code is generated:
the variant's value is flattened with that type's ``struct`` calls
(``codegen/flat.py``), in LabVIEW's byte format (big-endian, sizes prepended),
and ``type string`` is
written as the literal list of 16-bit numbers of that type's descriptor
(``type_string.py``), so a reader of the descriptor works.

A variant whose type is not known when the code is generated has nothing to
flatten by: the whole ``Variant`` (value, type and attributes) is pickled,
``type string`` is ``None``, and the string is
read back only by Flattened String To Variant. A subVI that parses ``type string``
as a descriptor works only where the type is known. A known type that cannot be
flattened is a gap.

Terminals (from the primitives entry):
  in  index 0 = Variant
  out index 1 = type string (array of I16)
  out index 2 = data string
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.type_string import TypeStringError, encode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import Functions, UnsupportedLayout, layout_of, render_parts
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import add_flat_imports, terminal, unsupported_layout_error
from ._variant_flat import variant_static_type


@register_op_fragment("VARIANT_TO_FLATTENED_STRING")
def variant_to_flattened_string(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    type_term = terminal(node, "output", 1)
    data_term = terminal(node, "output", 2)
    wants_type = type_term is not None and ctx.is_wired(type_term.id)
    wants_data = data_term is not None and ctx.is_wired(data_term.id)
    if not (wants_type or wants_data):
        return CodeFragment.empty()
    source = terminal(node, "input", 0)
    value = (ctx.wired_input(source.id, node) if source else None) or "None"
    lv_type = variant_static_type(ctx, node)
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    if lv_type is None:
        flattened = f"pickle.dumps({value})"
        ctx.add_import("import pickle")
        numbers = "None"
    else:
        try:
            layout = layout_of(lv_type)
            numbers = repr(encode(lv_type))
        except UnsupportedLayout as error:
            raise unsupported_layout_error(node, error) from error
        except TypeStringError as error:
            raise ConversionGap(
                f"Variant To Flattened String has no type string for {error}", node
            ) from error
        fns = Functions(ctx, ">")
        flattened = render_parts(layout.flatten(f"{value}.value", True, 0, fns), ">")
    if wants_data:
        assert data_term is not None
        variable = ctx.make_output_var("data_string", node.id, terminal_id=data_term.id)
        statements.append(
            build_assign(variable, parse_expr(f"({flattened}).decode('latin-1')"))
        )
        bindings[data_term.id] = variable
    if wants_type:
        assert type_term is not None
        variable = ctx.make_output_var("type_string", node.id, terminal_id=type_term.id)
        statements.append(build_assign(variable, parse_expr(numbers)))
        bindings[type_term.id] = variable
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings=bindings)

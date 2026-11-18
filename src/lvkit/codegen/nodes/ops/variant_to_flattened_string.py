"""Variant To Flattened String (primResID 8203).

A variant is a ``Variant`` (``variants_module``). When the variant comes from To
Variant, the type is that of the wire fed to it, known when the code is generated:
the variant's value is flattened with that type's ``struct`` calls
(``codegen/flat.py``), in LabVIEW's byte format (big-endian, sizes prepended),
and ``type string`` is
written as the literal list of 16-bit numbers of that type's descriptor
(``type_string.py``), so a reader of the descriptor works.

A variant whose type is not known when the code is generated is flattened by the
type it records as the program runs (``Variant.type``), with the package's
``variants.variant_to_flattened`` (``codegen/flat_writer.py``): the same
big-endian format, read back by Flattened String To Variant. Its ``type string``
is the type string the variant records; a variant whose type records none (an
empty variant, a type ``type_string.encode`` does not write) or a type the writer
does not write raises ``NotImplementedError`` naming it where the node runs. A
known type that cannot be flattened is a gap.

Terminals (from the primitives entry):
  in  index 0 = Variant
  out index 1 = type string (array of I16)
  out index 2 = data string
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal
from lvkit.type_string import TypeStringError, encode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import Functions, UnsupportedLayout, layout_of, render_parts
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import add_flat_imports, terminal, unsupported_layout_error
from ._variant_flat import variant_static_label, variant_static_type


def _flatten_at_run_time(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    value: str,
    type_term: Terminal | None,
    data_term: Terminal | None,
) -> CodeFragment:
    """The type string and the data string of a variant whose type only the
    running program knows, from ``variants.variant_to_flattened``."""
    ctx.import_variant("variant_to_flattened")
    both = ctx.make_output_var("flattened", node.id)
    statements: list[ast.stmt] = [
        build_assign(both, parse_expr(f"variant_to_flattened({value})"))
    ]
    bindings: dict[str, str] = {}
    for term, name, position in (
        (type_term, "type_string", 0),
        (data_term, "data_string", 1),
    ):
        if term is not None:
            variable = ctx.make_output_var(name, node.id, terminal_id=term.id)
            statements.append(build_assign(variable, parse_expr(f"{both}[{position}]")))
            bindings[term.id] = variable
    return CodeFragment(statements=statements, bindings=bindings)


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
    if lv_type is None:
        return _flatten_at_run_time(
            node,
            ctx,
            value,
            type_term if wants_type else None,
            data_term if wants_data else None,
        )
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    try:
        layout = layout_of(lv_type)
        label = variant_static_label(ctx, node)
        numbers = repr(encode(lv_type, label or None))
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

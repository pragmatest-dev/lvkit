"""Unflatten From String (primResID 1165).

LabVIEW's Unflatten From String parses a flattened binary string (as produced by
Flatten To String) back into the type wired to the ``type`` prototype input. Unlike
Type Cast, a string or array carries a 4-byte length PREFIX when ``data includes
array or string size`` is true (the default), and ``byte order`` selects
big-endian, native or little-endian (0, 1, 2; default big-endian). Both are read
from their constants when the code is generated, and the ``struct`` calls are
written out for the output's type (``codegen/flat.py``).

Terminals (from the primitives.json entry):
  in  index  7 = type (prototype; its type is the output "value" type)
  in  index  9 = byte order (0=big-endian, 1=native, 2=little-endian)
  in  index 10 = data includes array or string size? (Boolean; default True)
  in  index 11 = binary string
  out index  2 = value (unflattened data, matches the type-prototype's type)
  out index  3 = rest of the binary string
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import BYTE_ORDER_PREFIX, UnsupportedLayout, layout_of
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import (
    add_flat_imports,
    constant_input,
    read_flattened,
    terminal,
    unsupported_layout_error,
)


@register_op_fragment("UNFLATTEN_FROM_STRING")
def unflatten_from_string(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    value_term = terminal(node, "output", 2)
    rest_term = terminal(node, "output", 3)
    wants_value = value_term is not None and ctx.is_wired(value_term.id)
    wants_rest = rest_term is not None and ctx.is_wired(rest_term.id)
    if not (wants_value or wants_rest):
        return CodeFragment.empty()
    string_term = terminal(node, "input", 11)
    text = (ctx.wired_input(string_term.id, node) if string_term else None) or "''"
    prefix = bool(constant_input(ctx, node, 10, True))
    order = BYTE_ORDER_PREFIX[int(constant_input(ctx, node, 9, 0))]
    try:
        layout = layout_of(value_term.lv_type if value_term else None)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    statements, value_var, rest = read_flattened(
        ctx, node, layout, text, order, prefix, value_term, wants_value
    )
    bindings: dict[str, str] = {}
    if value_var is not None:
        assert value_term is not None
        bindings[value_term.id] = value_var
    if wants_rest:
        assert rest_term is not None
        variable = ctx.make_output_var(
            "rest_of_string", node.id, terminal_id=rest_term.id
        )
        statements.append(
            build_assign(variable, parse_expr(f"{rest}.decode('latin-1')"))
        )
        bindings[rest_term.id] = variable
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings=bindings)

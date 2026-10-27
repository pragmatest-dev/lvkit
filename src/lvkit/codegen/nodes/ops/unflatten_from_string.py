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

import ast

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...errors import CodeGenError
from ...flat import (
    BYTE_ORDER_PREFIX,
    Functions,
    Integer,
    Real,
    UnsupportedLayout,
    layout_of,
)
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import add_flat_imports, terminal, unsupported_layout_error


def _constant(ctx: CodeGenContext, node: PrimitiveNode, index: int, default):
    """The value of an input that must be a constant, or ``default`` unwired."""
    term = terminal(node, "input", index)
    text = ctx.resolve(term.id) if term else None
    if text is None:
        return default
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as error:
        raise CodeGenError(
            "Unflatten From String needs its byte order and size inputs to be "
            "constants",
            node,
        ) from error


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
    text = (ctx.resolve(string_term.id) if string_term else None) or "''"
    prefix = bool(_constant(ctx, node, 10, True))
    order = BYTE_ORDER_PREFIX[int(_constant(ctx, node, 9, 0))]
    try:
        layout = layout_of(value_term.lv_type if value_term else None)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    fns = Functions(ctx, order)
    data = ctx.make_output_var("flattened", node.id)
    encoded = parse_expr(f"{text}.encode('latin-1')")
    statements: list[ast.stmt] = [build_assign(data, encoded)]
    bindings: dict[str, str] = {}
    if isinstance(layout, (Integer, Real)):
        read = f"struct.unpack_from({order + layout.code!r}, {data})"
        value = f"complex(*{read})" if len(layout.code) == 2 else f"{read}[0]"
        rest = f"{data}[{layout.width}:]"
        position = None
    else:
        position = ctx.make_output_var("position", node.id)
        value = None
        rest = f"{data}[{position}:]"
    if wants_value or position is not None:
        assert value_term is not None
        variable = ctx.make_output_var("value", node.id, terminal_id=value_term.id)
        if position is None:
            statements.append(build_assign(variable, parse_expr(value or "None")))
        else:
            statements.append(
                parse_expr_statement(
                    f"{variable}, {position} = {fns.reader(layout, prefix)}({data}, 0)"
                )
            )
        bindings[value_term.id] = variable
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


def parse_expr_statement(source: str) -> ast.stmt:
    return ast.parse(source).body[0]

"""Flatten To String (primResID 1164).

LabVIEW's Flatten To String converts ``anything`` to a flattened binary string:
the write side of Unflatten From String. A string or array that is the whole
value carries a 4-byte length PREFIX when ``prepend array or string size?`` is
true (the default), and ``byte order`` selects big-endian, native or
little-endian (0, 1, 2; default big-endian). Both are read from their constants
when the code is generated, and the ``struct`` calls are written out for the
type of ``anything`` (``codegen/flat.py``). The string is a Python ``str`` of one
character per byte, as every LabVIEW string is.

The ``type string`` output (7.x only) is a LabVIEW type descriptor, which has no
Python meaning: a node that wires it is a gap.

Terminals (from the primitives.json entry):
  in  index  8 = error in
  in  index  9 = byte order (0=big-endian, 1=native, 2=little-endian)
  in  index 10 = prepend array or string size? (Boolean; default True)
  in  index 11 = anything (the value to flatten)
  out index  0 = error out
  out index  2 = type string (7.x only)
  out index  3 = data string (the flattened value)
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import (
    BYTE_ORDER_PREFIX,
    Functions,
    UnsupportedLayout,
    layout_of,
    render_parts,
)
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import (
    add_flat_imports,
    constant_input,
    terminal,
    unsupported_layout_error,
)


@register_op_fragment("FLATTEN_TO_STRING")
def flatten_to_string(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    type_term = terminal(node, "output", 2)
    if type_term is not None and ctx.is_wired(type_term.id):
        raise ConversionGap(
            "Flatten To String's type string output is a LabVIEW type descriptor, "
            "which has no Python meaning",
            node,
        )
    data_term = terminal(node, "output", 3)
    if data_term is None or not ctx.is_wired(data_term.id):
        return CodeFragment.empty()
    source = terminal(node, "input", 11)
    value = (ctx.wired_input(source.id, node) if source else None) or "None"
    prefix = bool(constant_input(ctx, node, 10, True))
    order = BYTE_ORDER_PREFIX[int(constant_input(ctx, node, 9, 0))]
    try:
        layout = layout_of(source.lv_type if source else None)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    fns = Functions(ctx, order)
    flattened = render_parts(layout.flatten(value, prefix, 0, fns), order)
    variable = ctx.make_output_var("data_string", node.id, terminal_id=data_term.id)
    statements: list[ast.stmt] = [
        build_assign(variable, parse_expr(f"({flattened}).decode('latin-1')"))
    ]
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings={data_term.id: variable})

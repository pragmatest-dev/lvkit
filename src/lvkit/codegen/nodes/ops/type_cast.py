"""Type Cast (primResID 1166).

LabVIEW's Type Cast reinterprets the FLAT bytes of ``x`` as the type of a
prototype input: it flattens ``x`` and unflattens the bytes as the new type. The
source type is the data input's (index 0) and the destination type is the
output's (index 2, whose type mirrors the type-prototype input at index 1); both
come from the graph, so the ``struct`` calls are written out for those types
(``codegen/flat.py``). NI: "When x is shorter than the destination this function
moves the data to the upper bytes of type and fills the remaining bytes with
zeros", so a shorter value is padded with zero bytes on the right.

A cast whose source and destination type are the same, or from one refnum to
another (an opaque handle, not bytes), keeps the value as it is.

Terminals (from the primitives.json entry):
  in  index 0 = x (data to reinterpret)
  in  index 1 = type (prototype; its type is the output type)
  out index 2 = x cast to the prototype's type
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import (
    BYTE_ORDER_PREFIX,
    Functions,
    Integer,
    Real,
    UnsupportedLayout,
    layout_of,
    render_parts,
)
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import add_flat_imports, terminal, unsupported_layout_error


def _is_refnum(lt: LVType | None) -> bool:
    return lt is not None and lt.underlying_type == "Refnum"


@register_op_fragment("TYPE_CAST")
def type_cast(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    source = terminal(node, "input", 0)
    result = terminal(node, "output", 2)
    if result is None or not ctx.is_wired(result.id):
        return CodeFragment.empty()
    value = (ctx.resolve(source.id) if source else None) or "None"
    src_type = source.lv_type if source else None
    dst_type = result.lv_type
    if (src_type is not None and src_type == dst_type) or (
        _is_refnum(src_type) and _is_refnum(dst_type)
    ):
        return CodeFragment(bindings={result.id: value})
    try:
        src = layout_of(src_type)
        dst = layout_of(dst_type)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    order = BYTE_ORDER_PREFIX[0]
    fns = Functions(ctx, order)
    flattened = render_parts(src.flatten(value, False, 0, fns), order)
    size = dst.fixed_size()
    if size is not None and (
        src.fixed_size() is None or (src.fixed_size() or 0) < size
    ):
        flattened = f"({flattened}).ljust({size}, b'\\x00')"
    data = ctx.make_output_var("flattened", node.id)
    variable = ctx.make_output_var("x_cast", node.id, terminal_id=result.id)
    statements: list[ast.stmt] = [build_assign(data, parse_expr(flattened))]
    if isinstance(dst, (Integer, Real)):
        fmt = order + dst.code
        read = f"struct.unpack_from({fmt!r}, {data})"
        expression = f"complex(*{read})" if len(dst.code) == 2 else f"{read}[0]"
    else:
        expression = f"{fns.reader(dst, False)}({data}, 0)[0]"
    statements.append(build_assign(variable, parse_expr(expression)))
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings={result.id: variable})

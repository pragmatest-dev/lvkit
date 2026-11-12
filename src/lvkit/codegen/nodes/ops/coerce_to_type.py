"""Coerce To Type (primResID 2402).

NI, "Coerce To Type Function" (labview-api-ref, ``functions/coerce-to-type.html``):
"Converts the input data to a compatible data type while preserving the data
value. Unlike the Type Cast function, this function does not reinterpret the input
data. Use this function ... To eliminate a coercion dot; To convert data without a
type definition to a compatible type definition or vice versa; To rename data on
the wire, such as a user event refnum." ``type`` only names the type: "LabVIEW
ignores any data in the constant or control wired to type". ``coerced x`` has the
type of ``type``.

* The same form of type in and out (a rename, an enum, a refnum, a typedef
  stripped of its name) is the value itself.
* A number to a number of another type follows LabVIEW's numeric conversion (NI,
  "Numeric Conversion"): a floating-point number to an integer is rounded to the
  nearest, a tie to the even one, and an out-of-range one is the integer type's
  largest or smallest value; an integer to a smaller integer keeps the least
  significant bits ("LabVIEW does not coerce out-of-range values to the minimum or
  maximum value of an integer"); a number to a floating-point or complex type is
  that type.
* Any other pair (a cluster that is a tuple in one form and a namespace in the
  other, an array of another element type) has no code generation yet.

An unwired ``x`` is the default of the type. Which input is ``x`` comes from the
callers' wiring: the user event refnum constant that only names a type is input 1
and the event data from Read Event is input 0 (``docs/_internal/design/
labview-binary-format.md``, "Coerce To Type").

Terminals (from the primitives entry): in 0 = x, 1 = type; out 2 = coerced x.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...type_default import type_default
from . import register_op_fragment
from ._flat_node import terminal
from ._numeric import format_kind
from ._regex_node import input_expression, string_variable


def _same_form(a: LVType, b: LVType) -> bool:
    """Whether a value of ``a`` is, in Python, already a value of ``b``."""
    if a.kind != b.kind or a.underlying_type != b.underlying_type:
        return False
    if bool(a.typedef_name) != bool(b.typedef_name):
        return False
    if a.kind == LVTypeKind.ARRAY:
        return (
            a.element_type is not None
            and b.element_type is not None
            and _same_form(a.element_type, b.element_type)
        )
    if a.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        fields_a, fields_b = a.fields or [], b.fields or []
        return len(fields_a) == len(fields_b) and all(
            fa.type is not None and fb.type is not None and _same_form(fa.type, fb.type)
            for fa, fb in zip(fields_a, fields_b, strict=True)
        )
    return True


def _numeric_conversion(source: LVType, target: LVType, value: str) -> str | None:
    """The expression that converts the number ``value`` of type ``source`` to
    ``target``; None when either is not a plain number."""
    if source.kind != LVTypeKind.PRIMITIVE or target.kind != LVTypeKind.PRIMITIVE:
        return None
    src, dst = format_kind(source), format_kind(target)
    if dst.kind == "float":
        return f"float({value})" if src.kind in ("int", "float") else None
    if dst.kind == "complex":
        return f"complex({value})" if src.kind in ("int", "float", "complex") else None
    if dst.kind != "int":
        return None
    low = -(2 ** (dst.bits - 1)) if dst.signed else 0
    high = 2 ** (dst.bits - 1) - 1 if dst.signed else 2**dst.bits - 1
    if src.kind == "float":
        return (
            f"({low} if {value} < {low} else "
            f"{high} if {value} > {high} else round({value}))"
        )
    if src.kind == "int":
        src_low = -(2 ** (src.bits - 1)) if src.signed else 0
        src_high = 2 ** (src.bits - 1) - 1 if src.signed else 2**src.bits - 1
        if low <= src_low and src_high <= high:
            return value
        return f"ctypes.c_{'' if dst.signed else 'u'}int{dst.bits}({value}).value"
    return None


@register_op_fragment("COERCE_TO_TYPE")
def coerce_to_type(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    x = terminal(node, "input", 0)
    out = terminal(node, "output", 2)
    if x is None or out is None or x.lv_type is None or out.lv_type is None:
        raise ConversionGap("Coerce To Type has no type on x or coerced x", node)
    wired = input_expression(ctx, node, 0)
    value = wired if wired is not None else ast.unparse(type_default(ctx, x.lv_type))
    if _same_form(x.lv_type, out.lv_type):
        return CodeFragment(bindings={out.id: value})
    statements: list[ast.stmt] = []
    name = string_variable(ctx, node, value, statements, "x")
    conversion = _numeric_conversion(x.lv_type, out.lv_type, name)
    if conversion is None:
        raise ConversionGap(
            f"Coerce To Type from {x.type_label()} to {out.type_label()} has no "
            "code generation yet",
            node,
        )
    if "ctypes." in conversion:
        ctx.add_import("import ctypes")
    var = ctx.make_output_var("coerced_x", node.id, terminal_id=out.id)
    statements.append(build_assign(var, parse_expr(conversion)))
    return CodeFragment(statements=statements, bindings={out.id: var})

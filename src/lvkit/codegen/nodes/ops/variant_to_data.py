"""Variant To Data (primResID 8003).

The node converts the variant's value to the type wired to ``type``; its
``data`` output has that type. A variant is the Python value itself, so the
generated code checks the value against the Python type of ``data``
(``Terminal.python_type``):

* a number converts to any numeric type (an integer type, an enum or a ring
  takes it rounded to the nearest integer, ties to even, as LabVIEW coerces a
  floating-point number to an integer; a floating-point type takes ``float``,
  a complex type ``complex``);
* any other value must already be of the data type's Python class;
* otherwise the node raises ``LabVIEWError`` code 91, NI's general error "The
  data type of the variant is not compatible with the data type wired to the
  type input"
  (https://docs-be.ni.com/api/bundle/labview-api-ref/page/errors/general-labview-error-codes.html).

A data type whose Python type is ``Any`` (a variant, a refnum, a type that did
not resolve) takes the value as it is. Which terminal is which is the catalog
entry's, by position, in the node's own numbering (LabVIEW versions number this
node two ways, ``primitive_resolver.terminal_sets``): the second input is
``variant``, the second output ``data``; a call without the ``variant`` input is
a ``TerminalNotOnCall`` gap. Errors are exceptions, so ``error in`` and ``error
out`` generate nothing.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import (
    COMPLEX_TYPE_NAMES,
    INTEGER_TYPE_NAMES,
    is_numeric_type_name,
)
from lvkit.models import LVType, LVTypeKind, Terminal, TerminalDirection
from lvkit.primitive_resolver import get_resolver
from lvkit.primitive_resolver.models import PrimitiveTerminal

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...errors import TerminalNotOnCall
from ...errors_module import LABVIEW_ERROR_NAME
from ...fragment import CodeFragment
from ...import_resolver import ImportResolver
from ...type_fields import annotation_imports
from . import register_op_fragment

INCOMPATIBLE_VARIANT_CODE = 91
INCOMPATIBLE_VARIANT_MESSAGE = (
    "The data type of the variant is not compatible with the data type wired to "
    "the type input."
)
_SOURCE = "Variant To Data"
# The catalog's terminals by position, in each direction: error in, variant,
# type / error out, data.
_VARIANT_POSITION = 1
_DATA_POSITION = 1


def _catalog_terminal(
    node: PrimitiveNode, direction: TerminalDirection, position: int
) -> PrimitiveTerminal | None:
    """The catalog terminal at ``position`` among the entry's ``direction``
    terminals, numbered as the node is."""
    resolved = get_resolver().resolve(
        prim_id=node.prim_id,
        node_slots={(t.index, t.direction) for t in node.terminals},
    )
    if resolved is None:
        return None
    terminals = sorted(
        (t for t in resolved.terminals if t.direction == direction),
        key=lambda t: t.index,
    )
    return terminals[position] if position < len(terminals) else None


def _node_terminal(
    node: PrimitiveNode, catalog: PrimitiveTerminal | None
) -> Terminal | None:
    if catalog is None:
        return None
    return next(
        (
            t
            for t in node.terminals
            if t.index == catalog.index and t.direction == catalog.direction
        ),
        None,
    )


def _numeric_conversion(lv_type: LVType) -> str | None:
    """The Python function that converts a number to ``lv_type``, or None when
    ``lv_type`` is not a numeric type."""
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return "round"
    underlying = lv_type.underlying_type or ""
    if lv_type.kind != LVTypeKind.PRIMITIVE or not is_numeric_type_name(underlying):
        return None
    if underlying in INTEGER_TYPE_NAMES:
        return "round"
    if underlying in COMPLEX_TYPE_NAMES:
        return "complex"
    return "float"


def _isinstance_target(annotation: ast.expr) -> ast.expr | None:
    """The class (or tuple of classes) an annotation's values are instances of;
    None for ``Any``, which every value is."""
    if isinstance(annotation, ast.Subscript):
        return _isinstance_target(annotation.value)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        parts = [
            _isinstance_target(annotation.left),
            _isinstance_target(annotation.right),
        ]
        if any(part is None for part in parts):
            return None
        return ast.Tuple(elts=[p for p in parts if p is not None], ctx=ast.Load())
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return parse_expr("type(None)")
    if isinstance(annotation, ast.Name):
        if annotation.id == "Any":
            return None
        if annotation.id == "None":
            return parse_expr("type(None)")
        return annotation
    if isinstance(annotation, ast.Attribute):
        return annotation
    return None


def _raise_incompatible() -> ast.Raise:
    return ast.Raise(
        exc=ast.Call(
            func=ast.Name(id=LABVIEW_ERROR_NAME, ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(
                    arg="code", value=ast.Constant(INCOMPATIBLE_VARIANT_CODE)
                ),
                ast.keyword(arg="source", value=ast.Constant(_SOURCE)),
                ast.keyword(
                    arg="message", value=ast.Constant(INCOMPATIBLE_VARIANT_MESSAGE)
                ),
            ],
        ),
        cause=None,
    )


def _type_imports(ctx: CodeGenContext, data: Terminal, annotation: str) -> None:
    """Import what the check names: a generated class or typedef, ``Path``."""
    if isinstance(ctx.import_resolver, ImportResolver):
        for statement in annotation_imports([data.lv_type], ctx.import_resolver):
            ctx.add_import(statement)
    if "Path" in {
        n.id for n in ast.walk(parse_expr(annotation)) if isinstance(n, ast.Name)
    }:
        ctx.add_import("from pathlib import Path")


@register_op_fragment("VARIANT_TO_DATA")
def variant_to_data(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    variant_entry = _catalog_terminal(
        node, TerminalDirection.INPUT, _VARIANT_POSITION
    )
    data = _node_terminal(
        node, _catalog_terminal(node, TerminalDirection.OUTPUT, _DATA_POSITION)
    )
    if variant_entry is None:
        raise ConversionGap(
            "the catalog entry of Variant To Data has no terminal for this call's "
            "numbering",
            node,
        )
    if data is None or not ctx.is_wired(data.id):
        return CodeFragment.empty()
    variant = _node_terminal(node, variant_entry)
    if variant is None:
        raise TerminalNotOnCall(
            node.prim_id or "",
            _SOURCE,
            variant_entry.name or str(variant_entry.index),
            variant_entry.type,
            node,
        )
    value = ctx.wired_input(variant.id, node) or "None"
    lv_type = data.lv_type
    if lv_type is None:
        return CodeFragment(bindings={data.id: value})
    # The check and the conversion read the value; an expression is read once.
    prelude: list[ast.stmt] = []
    if not value.isidentifier():
        local = ctx.make_output_var("variant", node.id)
        prelude.append(build_assign(local, parse_expr(value)))
        value = local

    convert = _numeric_conversion(lv_type)
    if convert is not None:
        accepted = "(int, float, complex)" if convert == "complex" else "(int, float)"
        mismatch = parse_expr(
            f"isinstance({value}, bool) or not isinstance({value}, {accepted})"
        )
        result = parse_expr(f"{convert}({value})")
    else:
        annotation = data.python_type(ctx.type_namer())
        target = _isinstance_target(parse_expr(annotation))
        if target is None:
            return CodeFragment(bindings={data.id: value})
        _type_imports(ctx, data, annotation)
        mismatch = ast.UnaryOp(
            op=ast.Not(),
            operand=ast.Call(
                func=ast.Name(id="isinstance", ctx=ast.Load()),
                args=[parse_expr(value), target],
                keywords=[],
            ),
        )
        result = parse_expr(value)

    ctx.import_labview_error()
    variable = ctx.make_output_var("data", node.id, terminal_id=data.id)
    statements: list[ast.stmt] = [
        *prelude,
        ast.If(test=mismatch, body=[_raise_incompatible()], orelse=[]),
        build_assign(variable, result),
    ]
    return CodeFragment(statements=statements, bindings={data.id: variable})

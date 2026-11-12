"""Variant To Data (primResID 8003).

The node converts the variant's value to the type wired to ``type``; its
``data`` output has that type. A variant is the Python value itself, so the
generated code checks the value against the annotation the generated code gives
``data`` (``ast_utils.terminal_annotation``; an error cluster is
``LabVIEWError | None``):

* a number converts to any numeric type (an integer type, an enum or a ring
  takes it rounded to the nearest integer, ties to even, as LabVIEW coerces a
  floating-point number to an integer; a floating-point type takes ``float``,
  a complex type ``complex``);
* any other value must already be of the data type's Python class;
* otherwise the node fails with a ``TypeError`` of NI's message for general
  error 91, "The data type of the variant is not compatible with the data type
  wired to the type input."
  (https://docs-be.ni.com/api/bundle/labview-api-ref/page/errors/general-labview-error-codes.html);
  the catalog lists ``TypeError`` as code 91, so it is the node's error out
  (``native_failure``) and ``data`` its type's default.

A data type whose Python type is ``Any`` (a variant, a refnum, a type that did
not resolve) takes the value as it is. Which terminal is which is the catalog
entry's, by position, in the node's own numbering (LabVIEW versions number this
node two ways, ``primitive_resolver.terminal_sets``): the second input is
``variant``, the second output ``data``; a call without the ``variant`` input is
a ``TerminalNotOnCall`` gap. The node runs only when no error came in on its
``error in`` (``error_flow``). With ``data`` unwired and an error out wired, the
check still runs and its failure is the error out (a VI asks whether a value is
of a type that way); with neither wired the node does nothing.
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
from lvkit.primitive_resolver.models import PrimitiveTerminal

from ...ast_utils import build_assign, parse_expr, terminal_annotation
from ...context import CodeGenContext
from ...error_flow import own_error_terminals
from ...errors import TerminalNotOnCall
from ...fragment import CodeFragment
from ...import_resolver import ImportResolver
from ...type_fields import annotation_imports
from . import register_op_fragment

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
    ctx: CodeGenContext,
    node: PrimitiveNode,
    direction: TerminalDirection,
    position: int,
) -> PrimitiveTerminal | None:
    """The catalog terminal at ``position`` among the entry's ``direction``
    terminals, numbered as the node is."""
    resolved = ctx.primitive_catalog.resolve(
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
    """``raise TypeError(<NI's message for code 91>)``."""
    return ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="TypeError", ctx=ast.Load()),
            args=[ast.Constant(INCOMPATIBLE_VARIANT_MESSAGE)],
            keywords=[],
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
        ctx, node, TerminalDirection.INPUT, _VARIANT_POSITION
    )
    data = _node_terminal(
        node, _catalog_terminal(ctx, node, TerminalDirection.OUTPUT, _DATA_POSITION)
    )
    if variant_entry is None:
        raise ConversionGap(
            "the catalog entry of Variant To Data has no terminal for this call's "
            "numbering",
            node,
        )
    _, error_outs = own_error_terminals(node, ctx)
    data_wired = data is not None and ctx.is_wired(data.id)
    if not data_wired and not any(ctx.is_wired(t.id) for t in error_outs):
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
    if data is None:
        raise ConversionGap(
            "the catalog entry of Variant To Data has no data terminal for this "
            "call's numbering",
            node,
        )
    lv_type = data.lv_type
    if lv_type is None:
        return CodeFragment(bindings={data.id: value})
    convert = _numeric_conversion(lv_type)
    target: ast.expr | None = None
    if convert is None:
        annotation = terminal_annotation(data, ctx.type_namer())
        target = _isinstance_target(parse_expr(annotation))
        if target is None:
            # Every value is of the data type: the data is the value itself.
            return CodeFragment(bindings={data.id: value})
        _type_imports(ctx, data, annotation)
        if data.is_error_cluster:
            ctx.import_labview_error()

    # The check and the conversion read the value; an expression is read once.
    prelude: list[ast.stmt] = []
    if not value.isidentifier():
        local = ctx.make_output_var("variant", node.id)
        prelude.append(build_assign(local, parse_expr(value)))
        value = local

    if convert is not None:
        accepted = "(int, float, complex)" if convert == "complex" else "(int, float)"
        mismatch = parse_expr(
            f"isinstance({value}, bool) or not isinstance({value}, {accepted})"
        )
        result = parse_expr(f"{convert}({value})")
    else:
        assert target is not None
        mismatch = ast.UnaryOp(
            op=ast.Not(),
            operand=ast.Call(
                func=ast.Name(id="isinstance", ctx=ast.Load()),
                args=[parse_expr(value), target],
                keywords=[],
            ),
        )
        result = parse_expr(value)

    statements: list[ast.stmt] = [
        *prelude,
        ast.If(test=mismatch, body=[_raise_incompatible()], orelse=[]),
    ]
    if not data_wired:
        # Only the check is read, through the error out.
        return CodeFragment(statements=statements)
    variable = ctx.make_output_var("data", node.id, terminal_id=data.id)
    statements.append(build_assign(variable, result))
    return CodeFragment(statements=statements, bindings={data.id: variable})

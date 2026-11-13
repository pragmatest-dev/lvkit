"""Variant To Data (primResID 8003).

The node converts the variant's value to the type wired to ``type``; its
``data`` output has that type. A variant is a ``Variant`` (``variants_module``): its
value, its LabVIEW type and its attributes. The generated code judges the type
the variant records against the data type, in this order:

* a number type (an integer type, an enum or a ring, a floating-point type, a
  complex type, a typedef of one): the variant's type must be a number of any
  representation, and the value converts to the data type (an integer type, an
  enum or a ring takes it rounded to the nearest integer, ties to even, as LabVIEW
  coerces a floating-point number to an integer; a floating-point type takes
  ``float``, a complex type ``complex``). A Boolean is not a number;
* a class or another typedef (the data type has a name): the variant's type must
  have the same code and the same name; variants do not recognise inheritance, so
  a child class in the variant is not the parent class (NI, Variant To Data:
  "variants do not recognize inheritance hierarchies");
* any other type with a type-descriptor code: the variant's type must have the
  same code;
* a type with no code: the variant's value must be of the data type's Python class
  (``ast_utils.terminal_annotation``; an error cluster is ``LabVIEWError | None``).

An empty variant has no type, so it fails every one of them. A type that fails
makes the node fail with a ``TypeError`` of NI's message for general error 91,
"The data type of the variant is not compatible with the data type wired to the
type input."
(https://docs-be.ni.com/api/bundle/labview-api-ref/page/errors/general-labview-error-codes.html);
the catalog lists ``TypeError`` as code 91, so it is the node's error out
(``native_failure``) and ``data`` its type's default.

A data type that is a variant takes the variant as it is; one whose Python type is
``Any`` and has no code takes the variant's value. Which terminal is which is the
catalog entry's, by position, in the node's own numbering (LabVIEW versions number
this node two ways, ``primitive_resolver.terminal_sets``): the second input is
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
    FLOAT_TYPE_NAMES,
    INTEGER_TYPE_NAMES,
    TD_FULL_TYPE,
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
from ...variant_values import is_variant_type, variant_input
from ...variants_module import lv_type_code_and_name
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


# The type-descriptor codes of the number types (integers, floating-point and
# complex numbers): a variant holding any of them converts to a number type.
_NUMBER_CODES = tuple(
    sorted({int(TD_FULL_TYPE[n].value) for n in INTEGER_TYPE_NAMES | FLOAT_TYPE_NAMES})
)


def _descriptor(lv_type: LVType) -> tuple[int, str] | None:
    """The code and name of ``lv_type`` as a variant records them; None for a type
    with no type-descriptor code."""
    try:
        return lv_type_code_and_name(lv_type)
    except ConversionGap:
        return None


def _type_check(
    value: str, lv_type: LVType, convert: str | None
) -> tuple[str, str] | None:
    """The test that the variant ``value`` is not of the data type ``lv_type``
    (it is empty, or its type is not the one the data type accepts) and the
    expression of the data; None for a type with no code, which is judged on the
    Python class of the value."""
    described = _descriptor(lv_type)
    if convert is not None:
        own = [described[0]] if described is not None else []
        codes = tuple(sorted({*_NUMBER_CODES, *own}))
        accepted = f"{value}.type.code in {codes}"
        result = f"{convert}({value}.value)"
    elif lv_type.type_ref() is not None and described is not None:
        code, name = described
        accepted = f"{value}.type.code == {code} and {value}.type.name == {name!r}"
        result = f"{value}.value"
    elif described is not None:
        accepted = f"{value}.type.code == {described[0]}"
        result = f"{value}.value"
    else:
        return None
    return f"{value}.type is None or not ({accepted})", result


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
    value = variant_input(ctx, node, variant)
    if data is None:
        raise ConversionGap(
            "the catalog entry of Variant To Data has no data terminal for this "
            "call's numbering",
            node,
        )
    lv_type = data.lv_type
    if lv_type is None or is_variant_type(lv_type):
        return CodeFragment(bindings={data.id: value})
    convert = _numeric_conversion(lv_type)
    target: ast.expr | None = None
    if _type_check(value, lv_type, convert) is None:
        annotation = terminal_annotation(data, ctx.type_namer())
        target = _isinstance_target(parse_expr(annotation))
        if target is None:
            # Every value is of the data type: the data is the variant's value.
            return CodeFragment(bindings={data.id: f"{value}.value"})
        _type_imports(ctx, data, annotation)
        if data.is_error_cluster:
            ctx.import_labview_error()

    # The check and the conversion read the value; an expression is read once.
    prelude: list[ast.stmt] = []
    if not value.isidentifier():
        local = ctx.make_output_var("variant", node.id)
        prelude.append(build_assign(local, parse_expr(value)))
        value = local

    checked = _type_check(value, lv_type, convert)
    if checked is not None:
        mismatch = parse_expr(checked[0])
        result = parse_expr(checked[1])
    else:
        assert target is not None
        mismatch = ast.BoolOp(
            op=ast.Or(),
            values=[
                parse_expr(f"{value}.type is None"),
                ast.UnaryOp(
                    op=ast.Not(),
                    operand=ast.Call(
                        func=ast.Name(id="isinstance", ctx=ast.Load()),
                        args=[parse_expr(f"{value}.value"), target],
                        keywords=[],
                    ),
                ),
            ],
        )
        result = parse_expr(f"{value}.value")

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

"""Front Panel Window:Window Bounds (FP.WinBounds), the property of the VI class
that is the bounds of the front panel window.

NI (``properties-and-methods/vi-server/vi/fpwinbounds.html``): the four elements of
the cluster are the top, left, bottom and right values of the front panel window in
global screen coordinates; "When you read this property, LabVIEW returns the window
bounds of the VI. If the front panel of the VI is not open, LabVIEW returns the
window bounds of the VI in the position it was last saved."; Read/Write, settable
when the VI is running.

The bounds belong to the VI, so each generated function carries them as
``function.window_bounds``, set right after its ``def`` beside
``function.window_title`` (``window_bounds_statement``): the tuple ``(left, top,
right, bottom)`` in NI's Elements order (Left, Top, Right, Bottom) of the position
the file saves (``WindowProps.window_bounds``, labview-binary-format.md "A VI's
saved window bounds"), or None for a file that records none. A read gives the
attribute as the property's cluster and a write sets it from the cluster
(``read`` / ``write``): a cluster the diagram names (a type definition) is a
``SimpleNamespace`` of its labelled elements in that order, an anonymous one a
tuple.

On the VI's own window the node does the same through the VI's own function and a
write also calls ``live_io.set_window_bounds(bounds)`` (``own_window_properties``).
A write on another VI's reference only sets its attribute: that window belongs to
its own run, whose ``live_io`` the caller does not hold."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_FP_BOUNDS_PROPERTY_CODE
from lvkit.models import LVType, PropertyDef

from ..ast_utils import field_attribute_names, parse_expr, parse_stmt
from ..context import CodeGenContext

BOUNDS_ATTRIBUTE = "window_bounds"

# The elements of NI's Window Bounds cluster, in its order.
_ELEMENTS = 4
_BOUNDS_NAME = "_window_bounds"


def is_bounds_row(prop: PropertyDef) -> bool:
    """Whether the property row is Front Panel Window:Window Bounds, by its code."""
    return len(prop.segments) == 1 and (
        prop.segments[0].code == VI_FP_BOUNDS_PROPERTY_CODE
    )


def _attributes(
    lv_type: LVType | None, node: PrimitiveNode
) -> list[str] | None:
    """The attribute name of each element of the property's cluster, in order;
    None for an anonymous cluster (a tuple). A type that is not a cluster of four
    elements is a gap."""
    if lv_type is None or not lv_type.fields or len(lv_type.fields) != _ELEMENTS:
        raise ConversionGap(
            "Front Panel Window:Window Bounds: the property's terminal is not a "
            f"cluster of the {_ELEMENTS} elements NI's Window Bounds has",
            node,
        )
    if not lv_type.typedef_name and not lv_type.classname:
        return None
    return field_attribute_names(f.name for f in lv_type.fields)


def read(
    reference: str, lv_type: LVType | None, node: PrimitiveNode, ctx: CodeGenContext
) -> ast.expr:
    """The bounds of the VI whose function is ``reference``, as the cluster the
    property's terminal carries (None where the file records none)."""
    attributes = _attributes(lv_type, node)
    held = f"{reference}.{BOUNDS_ATTRIBUTE}"
    if attributes is None:
        return parse_expr(held)
    ctx.add_import("from types import SimpleNamespace")
    fields = ", ".join(
        f"{name}={_BOUNDS_NAME}[{i}]" for i, name in enumerate(attributes)
    )
    return parse_expr(
        f"None if ({_BOUNDS_NAME} := {held}) is None else SimpleNamespace({fields})"
    )


def write(
    reference: str,
    value: str,
    lv_type: LVType | None,
    node: PrimitiveNode,
) -> list[ast.stmt]:
    """Set the bounds of the VI whose function is ``reference`` from the cluster
    ``value``."""
    attributes = _attributes(lv_type, node)
    held = f"{reference}.{BOUNDS_ATTRIBUTE}"
    if attributes is None:
        return [parse_stmt(f"{held} = tuple({value})")]
    return [
        parse_stmt(f"{_BOUNDS_NAME} = {value}"),
        parse_stmt(
            f"{held} = ({', '.join(f'{_BOUNDS_NAME}.{name}' for name in attributes)})"
        ),
    ]


def window_bounds_statement(ctx: CodeGenContext, key: str, target: str) -> ast.stmt:
    """``target.window_bounds = (left, top, right, bottom)`` for the VI ``key``:
    the bounds its file saves, else None."""
    bounds = None
    if ctx.graph is not None:
        bounds = ctx.graph.get_vi_properties(key).window.window_bounds
    return parse_stmt(f"{target}.{BOUNDS_ATTRIBUTE} = {bounds!r}")

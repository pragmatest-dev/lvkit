"""Display:All Monitors (Disp.AllMonitors), the property of the Application class that
lists the computer's monitors.

NI (``properties-and-methods/vi-server/application/dispallmon.html``): "Returns
information about all the monitors on the computer, including their bit depths and
bounding rectangles"; each element is a cluster of Bounds (Left, Top, Right, Bottom)
and Depth. Python has no screen to ask, so the function asks ``live_io``
(``live_io.monitors()``, ``(left, top, right, bottom, depth)`` for each monitor): the
window system that hosts the call knows, and the base class has none, so a function
called without a ``live_io`` that shows windows reads an empty array. The array is
built in the cluster types the property's terminal carries: a cluster the diagram
names (a type definition) is a ``SimpleNamespace`` of its labelled elements, an
anonymous one a tuple (``front_panel_bounds``)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import APP_ALL_MONITORS_PROPERTY_CODE, APP_CLASS
from lvkit.models import LVType, PropertyDef, Terminal

from ..ast_utils import LIVE_IO, field_attribute_names, parse_expr
from ..context import CodeGenContext

_ELEMENT_FIELDS = 2


def _cluster(
    lv_type: LVType | None, values: list[str], what: str, node: PrimitiveNode
) -> str:
    """The expression of a cluster of ``lv_type`` holding ``values``, in field order."""
    if lv_type is None or not lv_type.fields or len(lv_type.fields) != len(values):
        raise ConversionGap(
            f"Display:All Monitors: the {what} of the property's terminal is not a "
            f"cluster of {len(values)} elements",
            node,
        )
    if not lv_type.typedef_name and not lv_type.classname:
        return f"({', '.join(values)},)"
    names = field_attribute_names(f.name for f in lv_type.fields)
    return (
        "SimpleNamespace(" + ", ".join(f"{n}={v}" for n, v in zip(names, values)) + ")"
    )


def read(
    class_name: str,
    prop: PropertyDef,
    term: Terminal,
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> ast.expr | None:
    """The array of monitors for a read of Display:All Monitors on the default
    Application object; None for any other row."""
    if (
        class_name != APP_CLASS
        or len(prop.segments) != 1
        or prop.segments[0].code != APP_ALL_MONITORS_PROPERTY_CODE
        or term.direction != "output"
    ):
        return None
    array = term.lv_type
    element = array.element_type if array is not None else None
    fields = element.fields if element is not None else None
    if not fields or len(fields) != _ELEMENT_FIELDS:
        raise ConversionGap(
            "Display:All Monitors: the property's terminal is not an array of "
            "clusters of the Bounds and Depth NI's page gives",
            node,
        )
    bounds = _cluster(
        fields[0].type,
        ["left", "top", "right", "bottom"],
        "Bounds",
        node,
    )
    made = _cluster(element, [bounds, "depth"], "element", node)
    ctx.add_import("from types import SimpleNamespace")
    return parse_expr(
        f"[{made} for left, top, right, bottom, depth in {LIVE_IO}.monitors()]"
    )

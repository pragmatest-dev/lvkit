"""The rows of a Property Node on a front-panel object that are where the object is
and what it belongs to: Position (and its Left and Top), Bounds (and its Area Width
and Area Height), Owning VI, and the Origin of a pane.

NI's pages (``properties-and-methods/vi-server/generic/``): Position, "Position of
the top left corner of the bounding rectangle of the object on the front panel
window in terms of left and top coordinates" (``gobject/position.html``, with
``left.html`` and ``top.html``: read and write, settable while the VI runs); Bounds,
"Maximum bounding area of the object specified in terms of width and height in
pixels", read only (``gobject/bounds.html``, ``areawidth.html``, ``areaheight.html``);
Owning VI, "Returns a reference to the VI that owns this object", read only
(``owningvi.html``); Owner, "Reference to the owning object ... An object of class
Panel does not have an owner (it is the top-level object) and always returns Not A
Refnum. All objects in LabVIEW have an owning object. For example, the front panel
and block diagram are the owning objects of all the objects contained within each,
including controls and indicators. However, if a control is a cluster, the cluster
would the owning object of all the objects inside it", read only
(``owner2.html``); Origin of a Pane, "A cluster that contains the vertical and
horizontal coordinates of the upper left corner of the pane", read and write
(``gobject/pane/origin.html``).

Each is a member of the object the reference holds (``GObject`` of
``panel_objects_module.py``): ``position`` (left, top), ``left``, ``top``, ``bounds``
(width, height), ``area_width``, ``area_height``, ``owning_vi``, ``owner`` (the pane
or cluster that holds the object,
the panel for a pane, None for the panel) and, of a pane,
``origin`` (horizontal, vertical). A cluster the diagram names (a type definition)
is a ``SimpleNamespace`` of its labelled elements, an anonymous one a tuple
(``front_panel_bounds.cluster_read``). A write of a row NI says cannot be set while
the VI runs is a gap quoting the page, as for every other property.

The rows are told by their property code, which every class that has the property
shares. The same code generates the row on a wired reference
(``as_object(reference)``) and on a Property Node bound to a control of the VI
(``panel_of(this VI).control_of(name)``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, PropertyDef

from ..ast_utils import parse_expr, parse_stmt
from ..context import CodeGenContext
from ..panels_module import (
    AS_OBJECT_NAME,
    AS_OWNED_NAME,
    AS_PANE_NAME,
    PANEL_ERROR_NAME,
)
from . import front_panel_bounds

_PAGES = (
    "https://docs-be.ni.com/api/bundle/labview-api-ref/page/properties-and-methods/"
    "vi-server/generic/"
)


@dataclass(frozen=True)
class GeometryRow:
    """One row: its property ``code``, NI's ``name``, the ``member`` of the
    object, the number of elements of the cluster it is (0 for a single value),
    whether it ``settable`` while the VI runs, NI's page and the accessor of the
    panels module that gives the object the reference holds."""

    code: int
    name: str
    member: str
    count: int
    settable: bool
    page: str
    accessor: str = AS_OBJECT_NAME


OWNER_CODE = 103970822
"""The Owner property: the object that holds the object the reference refers to."""

_ROWS = (
    GeometryRow(103983104, "Position", "position", 2, True, "gobject/position"),
    GeometryRow(103983105, "Position:Left", "left", 0, True, "gobject/left"),
    GeometryRow(103983106, "Position:Top", "top", 0, True, "gobject/top"),
    GeometryRow(103983107, "Bounds", "bounds", 2, False, "gobject/bounds"),
    GeometryRow(
        103983108, "Bounds:Area Width", "area_width", 0, False, "gobject/areawidth"
    ),
    GeometryRow(
        103983109, "Bounds:Area Height", "area_height", 0, False, "gobject/areaheight"
    ),
    GeometryRow(103970818, "Owning VI", "owning_vi", 0, False, "owningvi"),
    GeometryRow(OWNER_CODE, "Owner", "owner", 0, False, "owner2", AS_OWNED_NAME),
    GeometryRow(
        404121605, "Origin", "origin", 2, True, "gobject/pane/origin", AS_PANE_NAME
    ),
)
_BY_CODE = {row.code: row for row in _ROWS}


def geometry_row(prop: PropertyDef) -> GeometryRow | None:
    """The geometry row the Property Node row ``prop`` is, by its property code; None
    for any other row."""
    codes = [segment.code for segment in prop.segments]
    return _BY_CODE.get(codes[0]) if len(codes) == 1 else None


def reference_target(row: GeometryRow, reference: str, ctx: CodeGenContext) -> str:
    """The expression of the object the reference ``reference`` holds, as the row
    reads it (error 1026 for a reference that holds none)."""
    ctx.import_panels(f"{row.accessor}, {PANEL_ERROR_NAME}")
    return f"{row.accessor}({reference})"


def page_url(row: GeometryRow) -> str:
    """NI's page for the row."""
    return f"{_PAGES}{row.page}.html"


def check_writable(row: GeometryRow, node: PrimitiveNode) -> None:
    """Raise the gap for a write of a row NI says cannot be set while the VI runs."""
    if not row.settable:
        raise ConversionGap(
            f"Property '{row.name}' on a {node.object_name} reference is written, "
            "and NI's page says it cannot be set while the VI runs "
            f"({page_url(row)})",
            node,
        )


def read(
    row: GeometryRow,
    target: str,
    lv_type: LVType | None,
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> ast.expr:
    """The value of the row on the object ``target``, as the cluster the property's
    terminal carries for a cluster row."""
    held = f"{target}.{row.member}"
    if row.count == 0:
        return parse_expr(held)
    return front_panel_bounds.cluster_read(
        held, lv_type, node, ctx, row.name, row.count
    )


def write(
    row: GeometryRow,
    target: str,
    value: str,
    lv_type: LVType | None,
    node: PrimitiveNode,
) -> list[ast.stmt]:
    """Set the row on the object ``target`` from ``value``."""
    check_writable(row, node)
    if row.count == 0:
        return [parse_stmt(f"{target}.{row.member} = {value}")]
    statements, elements = front_panel_bounds.cluster_tuple(
        value, lv_type, node, row.name, row.count
    )
    return [*statements, parse_stmt(f"{target}.{row.member} = {elements}")]

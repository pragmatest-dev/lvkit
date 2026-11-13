"""Property Node rows that read the front panel of a VI through its references.

A VI's front panel is its function's ``panel`` (``codegen/panels_module.py``); a
reference to the panel, and one to each control or indicator on it, are objects of
that module, and these rows read them:

- the ``Panel`` property of a VI reference (NI: properties-and-methods/vi-server/
  vi/frontpanel.html, "Reference to the front panel of a VI") is
  ``panel_of(reference)``;
- ``Controls[]`` of a panel reference (NI: .../generic/gobject/panel/controls.html,
  "Array of references to the controls and indicators on the front panel. The array
  contains the references in tabbing order") is ``as_panel(reference).controls``;
- ``Controls[]`` of a cluster reference (NI: .../generic/gobject/control/cluster/
  controls.html, "Array of references to the cluster elements in cluster order") is
  ``as_control(reference).controls``;
- ``Value`` of a control reference (NI: .../generic/gobject/control/value.html,
  "Data value of the control (LV variant)", read and write) is
  ``as_control(reference).variant``, read or assigned, where the property's terminal
  is a Variant, else ``.value``;
- ``Indicator`` (.../control/indicator.html, "If TRUE, the front panel object is an
  indicator. If FALSE, the object is a control") is ``.indicator``;
- ``Label.Text`` (.../control/label.html, "Reference to the label object. You can
  use this reference with the Text properties") is ``.label``.

A row is told by its property code and the class of the node (``PanelRow``), never
by the stored name. Indicator and Controls[] cannot be set while the VI runs (NI's
pages), and writing a label's text renames a control: those writes have no Python
meaning. A reference that holds no panel or control (Not a Refnum) is error 1026
(``panels_module.as_panel``, ``as_control``), reported on the node's error out.
"""

from __future__ import annotations

import ast
from enum import Enum

from lvkit.control_properties import ReferenceRow, reference_row
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_CLASS
from lvkit.models import PropertyDef

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..panels_module import (
    AS_CONTROL_NAME,
    AS_PANEL_NAME,
    PANEL_ERROR_NAME,
    PANEL_OF_NAME,
)

# The Panel property of the VI class: the code its Property Nodes record beside the
# names Panel and Front Panel.
VI_PANEL_PROPERTY_CODE = 573
# The Controls[] property of the Pnl class.
PANEL_CLASS = "Pnl"
PANEL_CONTROLS_PROPERTY_CODE = 104105985


class PanelRow(Enum):
    """Which row of the front panel's references a Property Node row is."""

    PANEL = "panel"
    CONTROLS = "controls"
    VALUE = "value"
    INDICATOR = "indicator"
    LABEL_TEXT = "label_text"
    CLUSTER_CONTROLS = "cluster_controls"


_REFERENCE_ROWS = {
    ReferenceRow.VALUE: PanelRow.VALUE,
    ReferenceRow.INDICATOR: PanelRow.INDICATOR,
    ReferenceRow.LABEL_TEXT: PanelRow.LABEL_TEXT,
    ReferenceRow.CLUSTER_CONTROLS: PanelRow.CLUSTER_CONTROLS,
}

_CONTROL_ATTRIBUTE = {
    PanelRow.VALUE: "value",
    PanelRow.INDICATOR: "indicator",
    PanelRow.LABEL_TEXT: "label",
    PanelRow.CLUSTER_CONTROLS: "controls",
}


def panel_row(node: PrimitiveNode, prop: PropertyDef) -> PanelRow | None:
    """The row of the front panel's references that the Property Node row ``prop``
    of ``node`` is; None for any other row."""
    codes = [segment.code for segment in prop.segments]
    class_name = node.object_name or ""
    if class_name == VI_CLASS:
        return PanelRow.PANEL if codes == [VI_PANEL_PROPERTY_CODE] else None
    if class_name == PANEL_CLASS:
        return PanelRow.CONTROLS if codes == [PANEL_CONTROLS_PROPERTY_CODE] else None
    found = reference_row(class_name, codes)
    return _REFERENCE_ROWS[found] if found is not None else None


def is_writable(row: PanelRow) -> bool:
    """Whether a write of the row has a Python meaning: a control's Value."""
    return row is PanelRow.VALUE


def _value_attribute(row: PanelRow, variant: bool) -> str:
    """The attribute of a control reference the row reads or writes: the Value row
    is the ``variant`` of the control when the property's terminal is a Variant."""
    if row is PanelRow.VALUE and variant:
        return "variant"
    return _CONTROL_ATTRIBUTE[row]


def read(
    row: PanelRow, reference: str, ctx: CodeGenContext, variant: bool
) -> ast.expr:
    """The Python value the row reads from the reference held by ``reference``;
    ``variant``: the property's terminal is a Variant."""
    if row is PanelRow.PANEL:
        names = (PANEL_OF_NAME, PANEL_ERROR_NAME)
        expression = f"{PANEL_OF_NAME}({reference})"
    elif row is PanelRow.CONTROLS:
        names = (AS_PANEL_NAME, PANEL_ERROR_NAME)
        expression = f"{AS_PANEL_NAME}({reference}).controls"
    else:
        names = (AS_CONTROL_NAME, PANEL_ERROR_NAME)
        expression = f"{AS_CONTROL_NAME}({reference}).{_value_attribute(row, variant)}"
    ctx.import_panels(", ".join(names))
    return parse_expr(expression)


def write(
    reference: str, value: str, ctx: CodeGenContext, variant: bool
) -> ast.stmt:
    """The assignment of ``value`` to the Value of the control ``reference`` holds;
    ``variant``: the property's terminal is a Variant."""
    ctx.import_panels(f"{AS_CONTROL_NAME}, {PANEL_ERROR_NAME}")
    return ast.Assign(
        targets=[
            ast.Attribute(
                value=parse_expr(f"{AS_CONTROL_NAME}({reference})"),
                attr=_value_attribute(PanelRow.VALUE, variant),
                ctx=ast.Store(),
            )
        ],
        value=parse_expr(value),
    )

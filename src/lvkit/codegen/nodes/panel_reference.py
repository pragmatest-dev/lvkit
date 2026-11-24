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
- ``Defer Panel Updates`` of a panel reference (NI: .../generic/gobject/panel/
  deferupdates.html, "When you set this property to TRUE, LabVIEW redraws any front
  panel objects with pending changes then defers all new requests for front panel
  updates. ... If FALSE, LabVIEW immediately redraws the changed elements of the
  front panel.", read and write, settable while the VI runs) is
  ``as_panel(reference).defer_updates``: the panel holds it (False until a VI sets
  it) and tells its views, which defer drawing while it is True;
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
  use this reference with the Text properties") is ``.label``;
- ``ClassID`` (.../generic/classid.html, "This integer uniquely identifies the class
  of a LabVIEW object") is ``.class_id``: NI's number of the control's VI Server
  class (``control_classes.json``); a control whose front-panel class has no
  recorded VI Server class raises NotImplementedError naming it, built when read;
- ``Array Element`` of an array reference (NI: .../generic/gobject/control/array/
  arrayelement.html, "Reference to the array element. The properties of the array
  apply to all elements in the array.", read only, not settable while the VI runs)
  is ``as_control(reference).array_element``: a reference to the element, a control
  of the element's class (``panels_module.Element``), whose own value, label and
  states the panel does not record (reading them raises NotImplementedError naming
  the array);
- ``Strings[]`` of a Combo Box reference (NI: .../control/string/combobox/
  strings.html, "Array of the strings from which you can select in the combo box
  control", read and write, settable while the VI runs) is
  ``as_control(reference).get_property("strings")`` / ``.set_property("strings",
  value)``: the panel holds the property like any other control property
  (``on_reference`` of its entry in ``control_properties.json``), a read gives the last
  write, else the strings the control is saved with, and its views show a write.

- ``Strings[]`` of an Enum reference and ``MechAction`` of a Boolean reference
  (``on_reference``, ``settable_while_running`` false: NI "you cannot write
  this property when the VI is running" for an enumerated type control, "Settable
  when the VI is running: No" for MechAction) are read the same way, from what the
  file saves (``saved`` of the panel); a write is a gap.

A row is told by its property code and the class of the node (``PanelRow``), never
by the stored name. Indicator and Controls[] cannot be set while the VI runs (NI's
pages), and writing a label's text renames a control: those writes have no Python
meaning. A reference that holds no panel or control (Not a Refnum) is error 1026
(``panels_module.as_panel``, ``as_control``), reported on the node's error out.
"""

from __future__ import annotations

import ast
from enum import Enum

from lvkit.control_properties import (
    ControlProperty,
    ReferenceRow,
    reference_property,
    reference_row,
)
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import is_on_vi_reference
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
# The Defer Panel Updates property of the Pnl class (the corpus records it as
# DeferPanUpdts and as Defer Panel Updates, with this code).
PANEL_DEFER_UPDATES_PROPERTY_CODE = 104105989


class PanelRow(Enum):
    """Which row of the front panel's references a Property Node row is."""

    PANEL = "panel"
    CONTROLS = "controls"
    DEFER_UPDATES = "defer_updates"
    VALUE = "value"
    INDICATOR = "indicator"
    LABEL_TEXT = "label_text"
    CLUSTER_CONTROLS = "cluster_controls"
    CLASS_ID = "class_id"
    ARRAY_ELEMENT = "array_element"


_REFERENCE_ROWS = {
    ReferenceRow.VALUE: PanelRow.VALUE,
    ReferenceRow.INDICATOR: PanelRow.INDICATOR,
    ReferenceRow.LABEL_TEXT: PanelRow.LABEL_TEXT,
    ReferenceRow.CLUSTER_CONTROLS: PanelRow.CLUSTER_CONTROLS,
    ReferenceRow.CLASS_ID: PanelRow.CLASS_ID,
    ReferenceRow.ARRAY_ELEMENT: PanelRow.ARRAY_ELEMENT,
}

_CONTROL_ATTRIBUTE = {
    PanelRow.VALUE: "value",
    PanelRow.INDICATOR: "indicator",
    PanelRow.LABEL_TEXT: "label",
    PanelRow.CLUSTER_CONTROLS: "controls",
    PanelRow.CLASS_ID: "class_id",
    PanelRow.ARRAY_ELEMENT: "array_element",
}


def panel_row(node: PrimitiveNode, prop: PropertyDef) -> PanelRow | None:
    """The row of the front panel's references that the Property Node row ``prop``
    of ``node`` is; None for any other row."""
    codes = [segment.code for segment in prop.segments]
    class_name = node.object_name or ""
    if is_on_vi_reference(node):
        return PanelRow.PANEL if codes == [VI_PANEL_PROPERTY_CODE] else None
    if class_name == PANEL_CLASS:
        if codes == [PANEL_CONTROLS_PROPERTY_CODE]:
            return PanelRow.CONTROLS
        if codes == [PANEL_DEFER_UPDATES_PROPERTY_CODE]:
            return PanelRow.DEFER_UPDATES
        return None
    found = reference_row(class_name, codes)
    return _REFERENCE_ROWS[found] if found is not None else None


def property_row(node: PrimitiveNode, prop: PropertyDef) -> ControlProperty | None:
    """The listed property of the control the Property Node row ``prop`` of ``node``
    reads or writes through the panel (``on_reference`` of
    ``control_properties.json``: a Combo Box's Strings[]); None for any other
    row."""
    if is_on_vi_reference(node):
        return None
    return reference_property(
        node.object_name or "", [segment.code for segment in prop.segments]
    )


def read_property(
    listed: ControlProperty, reference: str, ctx: CodeGenContext
) -> ast.expr:
    """The Python value of the property ``listed`` of the control ``reference``
    holds: the last write, else the state the control is saved in."""
    ctx.import_panels(f"{AS_CONTROL_NAME}, {PANEL_ERROR_NAME}")
    return parse_expr(f"{AS_CONTROL_NAME}({reference}).get_property({listed.member!r})")


def write_property(
    listed: ControlProperty, reference: str, value: str, ctx: CodeGenContext
) -> ast.stmt:
    """The call that sets the property ``listed`` of the control ``reference``
    holds to ``value``."""
    ctx.import_panels(f"{AS_CONTROL_NAME}, {PANEL_ERROR_NAME}")
    return ast.Expr(
        value=parse_expr(
            f"{AS_CONTROL_NAME}({reference}).set_property({listed.member!r}, {value})"
        )
    )


def is_writable(row: PanelRow) -> bool:
    """Whether a write of the row has a Python meaning: a control's Value or the
    panel's Defer Panel Updates."""
    return row in (PanelRow.VALUE, PanelRow.DEFER_UPDATES)


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
    elif row is PanelRow.DEFER_UPDATES:
        names = (AS_PANEL_NAME, PANEL_ERROR_NAME)
        expression = f"{AS_PANEL_NAME}({reference}).defer_updates"
    else:
        names = (AS_CONTROL_NAME, PANEL_ERROR_NAME)
        expression = f"{AS_CONTROL_NAME}({reference}).{_value_attribute(row, variant)}"
    ctx.import_panels(", ".join(names))
    return parse_expr(expression)


def write(
    row: PanelRow, reference: str, value: str, ctx: CodeGenContext, variant: bool
) -> ast.stmt:
    """The assignment of ``value`` to the Value of the control ``reference`` holds
    (``row`` Value; ``variant``: the property's terminal is a Variant), or to the
    Defer Panel Updates of the panel it holds."""
    if row is PanelRow.DEFER_UPDATES:
        ctx.import_panels(f"{AS_PANEL_NAME}, {PANEL_ERROR_NAME}")
        return ast.Assign(
            targets=[
                ast.Attribute(
                    value=parse_expr(f"{AS_PANEL_NAME}({reference})"),
                    attr="defer_updates",
                    ctx=ast.Store(),
                )
            ],
            value=parse_expr(value),
        )
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

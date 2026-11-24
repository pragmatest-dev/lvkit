"""SubPanel Insert VI and Remove VI, the methods of a SubPanel control that load a VI's
front panel into it and take it out.

NI (``.../vi-server/generic/gobject/control/subpanel/insertvi.html``):
"Loads a VI in the subpanel control without changing the state of the VI. If the
front panel of the VI you want to load is open or if you loaded the front panel in
another subpanel control on the same front panel, LabVIEW returns an error"; Remove VI
(``removevi.html``): "Unloads the VI in the subpanel control". The Invoke Node is bound
to a SubPanel control of the VI (``bound_control_uid``) or takes a reference to one.

A SubPanel holds no value, so the VI's panel does not list it among its controls: the
panel keeps the control by the identity the file gives it (the bound object's uid) or,
for a reference, by the name of the control it refers to, and tells its views
(``PanelView.subpanel_changed``): a view that has a widget for the SubPanel shows the
VI's panel in it, and with none attached the panel only records. The VI wired to Insert
VI is the reference of a function (its ``panel``); a reference that holds none is
error 1026, a VI whose front panel is open the node's error out
(``PanelValues.insert_vi``)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import (
    SUBPANEL_CLASS,
    SUBPANEL_INSERT_VI_METHOD_CODE,
    SUBPANEL_REMOVE_VI_METHOD_CODE,
)

from ..ast_utils import parse_stmt
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panels_module import (
    AS_CONTROL_NAME,
    PANEL_ERROR_NAME,
    PANEL_OF_NAME,
)
from . import ctrl_val
from .ctrl_val import report_panel_error

_METHODS = (SUBPANEL_INSERT_VI_METHOD_CODE, SUBPANEL_REMOVE_VI_METHOD_CODE)
# The VI Ref parameter of Insert VI: the first parameter, after the node's own.
_VI_REFERENCE = 6


def is_subpanel_method(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node of Insert VI or Remove VI of a SubPanel."""
    return node.object_name == SUBPANEL_CLASS and node.method_code in _METHODS


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The call on the panel that holds the SubPanel control."""
    reference_in = ctrl_val.terminal_at(node, "input", ctrl_val.REFERENCE_IN)
    wired = (
        ctx.wired_input(reference_in.id, node)
        if reference_in is not None and ctx.is_wired(reference_in.id)
        else None
    )
    if wired is not None:
        ctx.import_panels(f"{AS_CONTROL_NAME}, {PANEL_ERROR_NAME}")
        holder = f"{AS_CONTROL_NAME}({wired})"
        panel, control = f"{holder}.panel", f"{holder}.name"
    elif node.bound_control_uid:
        ctx.import_panels(f"{PANEL_OF_NAME}, {PANEL_ERROR_NAME}")
        panel = f"{PANEL_OF_NAME}({ctx.this_vi_expression()})"
        control = repr(str(node.bound_control_uid))
    else:
        raise ConversionGap(
            f"SubPanel {node.method_name}: the node is bound to no SubPanel control "
            "and has no reference wired",
            node,
        )
    if node.method_code == SUBPANEL_INSERT_VI_METHOD_CODE:
        vi = ctrl_val.input_value(node, ctx, _VI_REFERENCE, "")
        if not vi:
            raise ConversionGap(
                "SubPanel Insert VI: the VI Ref input is not wired", node
            )
        call = f"{panel}.insert_vi({control}, {vi})"
    else:
        call = f"{panel}.remove_vi({control})"
    bindings: dict[str, str] = {}
    reference_out = ctrl_val.terminal_at(node, "output", ctrl_val.REFERENCE_OUT)
    if reference_out is not None and wired is not None:
        bindings[reference_out.id] = wired
    body: ast.stmt = parse_stmt(call)
    return report_panel_error(node, ctx, [body], bindings)

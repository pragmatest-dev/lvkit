"""The one name table of a VI's front panel.

Each control and indicator of a VI has a name that is unique among all of them (a
control and an indicator may share a label, and so may two controls): a control's
is its parameter name (the variable that holds it, for a control off the connector
pane), an indicator's the field of the result that holds it (the variable name of
one off the connector pane), made distinct from the controls' and from each
other. The same name is the name the function passes ``live_io``
(``panel_hooks.PanelHooks``), the key the ``Panel`` holds the value under
(``panel_description``) and the name the NiceGUI view draws the widget with
(``lvkit.panel.model``); the label is only what a Ctrl Val method looks it up by.
"""

from __future__ import annotations

from lvkit.graph.models import VIContext
from lvkit.models import Terminal

from .ast_utils import distinct_names, interface_names, to_var_name
from .label_names import python_label


def _indicator_name(indicator: Terminal, fields: dict[str, str]) -> str:
    """The result field name of a connector-pane indicator, the variable name of
    one that is not."""
    return fields.get(indicator.id) or to_var_name(
        python_label(indicator) or "indicator"
    )


def panel_names(
    vi_context: VIContext, control_names: dict[str, str] | None = None
) -> dict[str, str]:
    """The name of each control and indicator of ``vi_context``, by terminal id:
    the controls first (``control_names``, terminal id to name, are the names a
    caller gave the parameters when they are not the connector pane's own), then
    the indicators, pane first."""
    interface = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, vi_context.outputs
    )
    given = control_names or interface.parameters
    controls = [*vi_context.inputs, *vi_context.off_pane_inputs]
    names = {c.id: given[c.id] for c in controls if c.id in given}
    indicators = [*vi_context.outputs, *vi_context.off_pane_outputs]
    wanted = [_indicator_name(t, interface.fields) for t in indicators]
    for indicator, name in zip(
        indicators, distinct_names(names.values(), wanted), strict=True
    ):
        names[indicator.id] = name
    return names

"""What a front panel needs to be built: one ``PanelControl`` for each control and
indicator of the VI, from its stored front panel and the terminals the graph ties
to it, in the names the generated function uses."""

from __future__ import annotations

from dataclasses import replace

from lvkit.codegen.ast_utils import interface_names, to_var_name
from lvkit.codegen.panel_hooks import PanelHooks
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import VIContext
from lvkit.models import FPTerminal, Terminal

from .kinds import PanelControl
from .shape import describe


def panel_controls(
    graph: InMemoryVIGraph,
    vi_name: str,
    vi_context: VIContext,
    hooks: PanelHooks | None,
) -> list[PanelControl]:
    """The controls and indicators of the VI's front panel, in the panel's own
    order."""
    panel = graph.get_vi_front_panel(vi_name)
    if panel is None:
        return []
    interface = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, vi_context.outputs
    )
    terminals: dict[str, Terminal] = {
        t.fp_dco_uid: t
        for t in [
            *vi_context.inputs,
            *vi_context.off_pane_inputs,
            *vi_context.outputs,
            *vi_context.off_pane_outputs,
        ]
        if isinstance(t, FPTerminal) and t.fp_dco_uid
    }
    on_pane_inputs = {t.id for t in vi_context.inputs}
    on_pane_outputs = {t.id for t in vi_context.outputs}
    controls: list[PanelControl] = []
    for control in panel.controls:
        terminal = terminals.get(control.uid)
        if terminal is None:
            controls.append(
                PanelControl(
                    label=control.name,
                    kind=None,
                    is_indicator=control.is_indicator,
                    bounds=control.bounds,
                    key=to_var_name(control.name),
                    member=None,
                    parameter=None,
                    field=None,
                    default=0,
                    integer=False,
                    gap="the panel needs the control's terminal on the block diagram",
                )
            )
            continue
        key = (
            hooks.names[terminal.id]
            if hooks is not None and terminal.id in hooks.names
            else interface.parameters.get(terminal.id)
            or interface.fields.get(terminal.id)
            or to_var_name(control.name)
        )
        controls.append(
            replace(
                describe(control, terminal.lv_type, terminal.default_value, key),
                member=(
                    hooks.members[terminal.id]
                    if hooks is not None and terminal.id in hooks.members
                    else None
                ),
                parameter=(
                    interface.parameters[terminal.id]
                    if terminal.id in on_pane_inputs
                    else None
                ),
                field=(
                    interface.fields[terminal.id]
                    if terminal.id in on_pane_outputs
                    else None
                ),
                required=terminal.id in on_pane_inputs
                and is_required(terminal, "input"),
            )
        )
    return controls

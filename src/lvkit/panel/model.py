"""What a front panel needs to be built: one ``PanelControl`` for each control and
indicator of the VI, from its stored front panel and the terminals the graph ties
to it, in the names the generated function uses."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from lvkit.codegen.ast_utils import interface_names, to_var_name
from lvkit.codegen.control_property_state import stored_states
from lvkit.codegen.panel_hooks import PanelHooks
from lvkit.codegen.panel_names import panel_names
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import PrimitiveNode, VIContext
from lvkit.models import FPTerminal, Terminal

from .kinds import PanelControl
from .shape import describe


def _used_properties(
    graph: InMemoryVIGraph, vi_name: str, terminals: Iterable[Terminal]
) -> dict[str, set[str]]:
    """The control properties of each control and indicator (by terminal id) that
    the panel has to show: the ones a Property Node of the VI writes or reads and
    the ones the file stores the control in."""
    used: dict[str, set[str]] = {}
    for node in graph.iter_nodes(vi_name):
        if isinstance(node, PrimitiveNode) and node.bound_control_uid:
            bound = graph.bound_terminal(node)
            if bound is not None:
                for _row, _term, prop in graph.listed_property_rows(node):
                    used.setdefault(bound.id, set()).add(prop.member)
    for terminal in terminals:
        if isinstance(terminal, FPTerminal):
            used.setdefault(terminal.id, set()).update(stored_states(terminal))
    return used


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
    names = panel_names(vi_context)
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
    used = _used_properties(graph, vi_name, terminals.values())
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
        key = names.get(terminal.id) or to_var_name(control.name)
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
                properties=tuple(sorted(used.get(terminal.id, ()))),
            )
        )
    return controls

"""What a front panel needs to be built: one ``PanelControl`` for each control and
indicator of the VI, from its stored front panel and the terminals the graph ties
to it, in the names the generated function uses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from lvkit.codegen.ast_utils import interface_names, to_var_name
from lvkit.codegen.panel_hooks import PanelHooks
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.models import FPTerminal, Terminal


class PanelKind(Enum):
    """The kinds of control the panel builds, each with the builder functions of
    the ``controls`` package that draw it."""

    NUMBER = ("number_control", "number_indicator", "stdNum", ("int", "float"))
    BOOLEAN = ("bool_control", "bool_indicator", "stdBool", ("bool",))
    STRING = ("string_control", "string_indicator", "stdString", ("str",))

    def __init__(
        self,
        control_builder: str,
        indicator_builder: str,
        control_type: str,
        python_types: tuple[str, ...],
    ) -> None:
        self.control_builder = control_builder
        self.indicator_builder = indicator_builder
        self.control_type = control_type
        self.python_types = python_types

    @classmethod
    def of(cls, control_type: str, python_type: str) -> PanelKind | None:
        for kind in cls:
            if kind.control_type == control_type and python_type in kind.python_types:
                return kind
        return None


@dataclass(frozen=True)
class PanelControl:
    """One control or indicator of the panel.

    ``key`` is the name the hooks pass for it and ``member`` the member of the
    VI's name enum that holds it (None when the VI has no hooks). ``parameter``
    is the keyword the function takes the control by (a connector-pane control)
    and ``field`` the result field the function returns the indicator in (a
    connector-pane indicator). ``kind`` is None for a control the panel cannot
    build; ``gap`` then says why."""

    label: str
    kind: PanelKind | None
    is_indicator: bool
    bounds: tuple[int, int, int, int]  # top, left, bottom, right
    key: str
    member: str | None
    parameter: str | None
    field: str | None
    default: str | int | float | bool
    integer: bool
    gap: str | None = None


def _default(kind: PanelKind | None, value: object) -> str | int | float | bool:
    if kind is PanelKind.BOOLEAN:
        return bool(value)
    if kind is PanelKind.STRING:
        return "" if value is None else str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return 0


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
            kind = None
            gap = "the panel needs the control's terminal on the block diagram"
            key, default, integer = to_var_name(control.name), 0, False
        else:
            python_type = terminal.python_type()
            kind = PanelKind.of(control.control_type, python_type)
            gap = (
                None
                if kind is not None
                else (
                    f"the panel has no widget for a {control.control_type} "
                    f"control of type {python_type}"
                )
            )
            key = (
                hooks.names[terminal.id]
                if hooks is not None and terminal.id in hooks.names
                else interface.parameters.get(terminal.id)
                or interface.fields.get(terminal.id)
                or to_var_name(control.name)
            )
            default = _default(kind, terminal.default_value)
            integer = python_type == "int"
        controls.append(
            PanelControl(
                label=control.name,
                kind=kind,
                is_indicator=control.is_indicator,
                bounds=control.bounds,
                key=key,
                member=(
                    hooks.members[terminal.id]
                    if hooks is not None
                    and terminal is not None
                    and terminal.id in hooks.members
                    else None
                ),
                parameter=(
                    interface.parameters[terminal.id]
                    if terminal is not None and terminal.id in on_pane_inputs
                    else None
                ),
                field=(
                    interface.fields[terminal.id]
                    if terminal is not None and terminal.id in on_pane_outputs
                    else None
                ),
                default=default,
                integer=integer,
                gap=gap,
            )
        )
    return controls

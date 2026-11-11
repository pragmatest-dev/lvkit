"""The label maps a generated function carries for its front panel.

``front_panels.FrontPanel`` sets and reads a VI's controls and indicators by the
label LabVIEW shows. Each generated function carries two function attributes,
set after its ``def`` (after its class, for a method): ``panel_controls`` maps
the label of each connector-pane control to the function's parameter, and
``panel_indicators`` the label of each connector-pane indicator to the field of
its result. A method whose class output is the object it was called on, changed
in place, returns no field for it: ``panel_passed`` maps that indicator's label to
the parameter (``self``) whose value it shows. A label two terminals share belongs
to the first, as the first object in the tabbing order answers a Ctrl Val method.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from lvkit.graph.models import VIContext
from lvkit.models import Terminal

from .ast_utils import interface_names, parse_expr

PANEL_CONTROLS = "panel_controls"
PANEL_INDICATORS = "panel_indicators"
PANEL_PASSED = "panel_passed"


def labelled(pairs: Iterable[tuple[Terminal, str]]) -> dict[str, str]:
    """Label to Python name of each terminal that has a label, the first
    terminal of a label keeping it."""
    labels: dict[str, str] = {}
    for terminal, name in pairs:
        if terminal.name:
            labels.setdefault(terminal.name, name)
    return labels


def function_label_maps(vi_context: VIContext) -> tuple[dict[str, str], dict[str, str]]:
    """``(panel_controls, panel_indicators)`` of a VI's function: its parameters
    and the fields of its result (``interface_names``)."""
    parameters = interface_names(vi_context.inputs, [], []).parameters
    fields = interface_names([], [], vi_context.outputs).fields
    return (
        labelled((t, parameters[t.id]) for t in vi_context.inputs),
        labelled((t, fields[t.id]) for t in vi_context.outputs),
    )


def attribute_statements(
    target: str,
    controls: dict[str, str],
    indicators: dict[str, str],
    passed: dict[str, str] | None = None,
) -> list[ast.stmt]:
    """``target.panel_controls = {...}``, ``target.panel_indicators = {...}`` and
    ``target.panel_passed = {...}``, ``target`` the expression of the function; a
    map with no entry is not set (the panel reads an absent attribute as empty)."""
    return [
        ast.Assign(
            targets=[
                ast.Attribute(value=parse_expr(target), attr=attribute, ctx=ast.Store())
            ],
            value=ast.Dict(
                keys=[ast.Constant(value=label) for label in labels],
                values=[ast.Constant(value=name) for name in labels.values()],
            ),
        )
        for attribute, labels in (
            (PANEL_CONTROLS, controls),
            (PANEL_INDICATORS, indicators),
            (PANEL_PASSED, passed or {}),
        )
        if labels
    ]

"""The label maps a generated function carries for its front panel.

``front_panels.FrontPanel`` sets and reads a VI's controls and indicators by the
label LabVIEW shows. Each generated function carries two function attributes,
set after its ``def`` (after its class, for a method): ``panel_controls`` maps
the label of each connector-pane control to the function's parameter, and
``panel_indicators`` the label of each connector-pane indicator to the field of
its result. A control whose parameter has no default (a Required input) holds
its default until a run sets it: ``panel_defaults`` maps its label to a function
that makes the control's default (its saved default, else its type's, a class's
default object), so each run gets a fresh list or object. A method whose class
output is the object it was called on, changed in place, returns no field for it:
``panel_passed`` maps that indicator's label to the parameter (``self``) whose
value it shows. A label two terminals share belongs to the first, as the first
object in the tabbing order answers a Ctrl Val method.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import VIContext
from lvkit.models import Terminal

from .ast_utils import interface_names, parse_expr
from .type_default import control_default

if TYPE_CHECKING:
    from .context import CodeGenContext

PANEL_CONTROLS = "panel_controls"
PANEL_INDICATORS = "panel_indicators"
PANEL_PASSED = "panel_passed"
PANEL_DEFAULTS = "panel_defaults"


def labelled(pairs: Iterable[tuple[Terminal, str]]) -> dict[str, str]:
    """Label to Python name of each terminal that has a label, the first
    terminal of a label keeping it."""
    labels: dict[str, str] = {}
    for terminal, name in pairs:
        if terminal.name:
            labels.setdefault(terminal.name, name)
    return labels


@dataclass
class PanelDefaults:
    """The ``panel_defaults`` of a function: label to the expression of a
    zero-argument function making the control's default, and the ``def``
    statements those expressions name (``statements``)."""

    entries: dict[str, ast.expr] = field(default_factory=dict)
    statements: list[ast.stmt] = field(default_factory=list)


def _no_parameters() -> ast.arguments:
    return ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
    )


def required_defaults(
    terminals: Iterable[Terminal], ctx: CodeGenContext, target: str, lazy: bool
) -> PanelDefaults:
    """The defaults of each label whose control (the first terminal of the label)
    is a Required input, the control a direct call must pass: a ``lambda`` making
    the default. In a module that imports other generated modules inside its
    functions (``lazy``), a default that needs one is a ``def`` of ``target``'s
    name that imports it, as the function bodies do. A control whose default
    needs a class that is not loaded has no entry: the panel holds ``None`` for
    it (the class is reported where the VI uses it)."""
    first: dict[str, Terminal] = {}
    for terminal in terminals:
        if terminal.name:
            first.setdefault(terminal.name, terminal)
    defaults = PanelDefaults()
    stem = target.replace(".", "_")
    for label, terminal in first.items():
        if not is_required(terminal, "input"):
            continue
        module_imports, ctx.imports = ctx.imports, set()
        try:
            value = control_default(ctx, terminal)
        except ConversionGap:
            ctx.imports = module_imports
            continue
        added, ctx.imports = ctx.imports, module_imports
        needed = {i for i in added if i.startswith("from .")} if lazy else set()
        ctx.imports |= added - needed
        if needed:
            name = f"_{stem}_default_{len(defaults.entries)}"
            imports = (stmt for i in sorted(needed) for stmt in ast.parse(i).body)
            defaults.statements.append(
                ast.FunctionDef(
                    name=name,
                    args=_no_parameters(),
                    body=[*imports, ast.Return(value=value)],
                    decorator_list=[],
                )
            )
            defaults.entries[label] = ast.Name(id=name, ctx=ast.Load())
        else:
            defaults.entries[label] = ast.Lambda(args=_no_parameters(), body=value)
    return defaults


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
    defaults: PanelDefaults | None = None,
) -> list[ast.stmt]:
    """``target.panel_controls = {...}``, ``target.panel_indicators = {...}``,
    ``target.panel_passed = {...}`` and ``target.panel_defaults = {...}``,
    ``target`` the expression of the function; a map with no entry is not set (the
    panel reads an absent attribute as empty)."""
    maps: list[tuple[str, Mapping[str, str | ast.expr]]] = [
        (PANEL_CONTROLS, controls),
        (PANEL_INDICATORS, indicators),
        (PANEL_PASSED, passed or {}),
        (PANEL_DEFAULTS, defaults.entries if defaults else {}),
    ]
    assignments: list[ast.stmt] = [
        ast.Assign(
            targets=[
                ast.Attribute(value=parse_expr(target), attr=attribute, ctx=ast.Store())
            ],
            value=ast.Dict(
                keys=[ast.Constant(value=label) for label in labels],
                values=[
                    name if isinstance(name, ast.expr) else ast.Constant(value=name)
                    for name in labels.values()
                ],
            ),
        )
        for attribute, labels in maps
        if labels
    ]
    return [*(defaults.statements if defaults else []), *assignments]

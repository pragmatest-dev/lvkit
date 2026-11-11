"""The description of a VI's front panel that a generated function carries.

Each generated function gets ``function.panel = Panel(function, ...)`` right after
its ``def`` (after its class, for a method), naming its controls and indicators by
the label LabVIEW shows (``codegen/panels_module.py`` holds ``Panel``):

- ``controls``: the label of each control to the parameter that takes it (None for
  a control off the connector pane) and the function that makes its default. A
  control whose parameter has no default (a Required input) holds its default until
  a run sets it, and so does a control that is not on the connector pane (no
  parameter, so a run cannot pass its value): the function is a ``lambda`` making
  the control's default (its saved default, else its type's, a class's default
  object), so each run gets a fresh list or object. Every other control's default
  is its parameter's own;
- ``indicators``: the label of each indicator to the field of the result that holds
  it (None for one off the connector pane);
- ``passed``: the label of a class output that is the object the method ran on to
  the parameter (``self``) whose value it shows, as a method whose class output is
  that object returns no field for it;
- ``duplicates``: the labels more than one control or indicator has. A Ctrl Val
  method picks "the first object whose label matches" in the front panel tabbing
  order, which lvkit does not read, so the panel raises for such a label.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import VIContext
from lvkit.models import Terminal

from .ast_utils import interface_names, parse_expr
from .panels_module import PANEL_NAME
from .type_default import control_default

if TYPE_CHECKING:
    from .context import CodeGenContext

# A terminal and the Python name that holds its value: the parameter of a control,
# the field of an indicator; None for a terminal that is not on the connector pane.
NamedTerminal = tuple[Terminal, str | None]


def duplicate_labels(*terminals: Iterable[Terminal]) -> list[str]:
    """The labels more than one of ``terminals`` (controls and indicators, on the
    connector pane or not) has, in first-seen order."""
    seen: set[str] = set()
    duplicates: dict[str, None] = {}
    for terminal in (t for group in terminals for t in group):
        if not terminal.name:
            continue
        if terminal.name in seen:
            duplicates[terminal.name] = None
        seen.add(terminal.name)
    return list(duplicates)


def panel_terminals(vi_context: VIContext) -> list[Terminal]:
    """Every control and indicator of a VI, on the connector pane or not."""
    return [
        *vi_context.inputs,
        *vi_context.off_pane_inputs,
        *vi_context.outputs,
        *vi_context.off_pane_outputs,
    ]


@dataclass
class DefaultFunctions:
    """The default functions of a ``Panel``: label to the expression of a
    zero-argument function making the control's default, and the ``def``
    statements those expressions name (``statements``)."""

    entries: dict[str, ast.expr] = field(default_factory=dict)
    statements: list[ast.stmt] = field(default_factory=list)


def _no_parameters() -> ast.arguments:
    return ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
    )


def default_functions(
    controls: Sequence[NamedTerminal], ctx: CodeGenContext, target: str, lazy: bool
) -> DefaultFunctions:
    """The default function of each label whose control (the first terminal of the
    label) is a Required input or is not on the connector pane: a ``lambda``
    making the default. In a module that imports other generated modules inside its
    functions (``lazy``), a default that needs one is a ``def`` of ``target``'s
    name that imports it, as the function bodies do. A control whose default needs
    a class that is not loaded has no entry: the panel holds ``None`` for it (the
    class is reported where the VI uses it)."""
    first: dict[str, NamedTerminal] = {}
    for terminal, name in controls:
        if terminal.name:
            first.setdefault(terminal.name, (terminal, name))
    defaults = DefaultFunctions()
    stem = target.replace(".", "_")
    for label, (terminal, name) in first.items():
        if name is not None and not is_required(terminal, "input"):
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
            function = f"_{stem}_default_{len(defaults.entries)}"
            imports = (stmt for i in sorted(needed) for stmt in ast.parse(i).body)
            defaults.statements.append(
                ast.FunctionDef(
                    name=function,
                    args=_no_parameters(),
                    body=[*imports, ast.Return(value=value)],
                    decorator_list=[],
                )
            )
            defaults.entries[label] = ast.Name(id=function, ctx=ast.Load())
        else:
            defaults.entries[label] = ast.Lambda(args=_no_parameters(), body=value)
    return defaults


def _first_by_label(pairs: Iterable[NamedTerminal]) -> dict[str, str | None]:
    """Label to Python name of each terminal that has a label, the first terminal
    of a label keeping it."""
    labels: dict[str, str | None] = {}
    for terminal, name in pairs:
        if terminal.name:
            labels.setdefault(terminal.name, name)
    return labels


def _name_map(
    keyword: str, pairs: Iterable[NamedTerminal]
) -> tuple[str, ast.Dict] | None:
    names = _first_by_label(pairs)
    if not names:
        return None
    return keyword, ast.Dict(
        keys=[ast.Constant(value=label) for label in names],
        values=[ast.Constant(value=name) for name in names.values()],
    )


def panel_statements(
    ctx: CodeGenContext,
    target: str,
    controls: Sequence[NamedTerminal],
    indicators: Sequence[NamedTerminal],
    all_terminals: Iterable[Terminal],
    lazy: bool,
    passed: Sequence[NamedTerminal] = (),
) -> list[ast.stmt]:
    """``target.panel = Panel(target, controls=..., indicators=..., passed=...,
    duplicates=...)``, ``target`` the expression of the function, preceded by the
    ``def`` of each default function a lazy module needs. A keyword with no entry
    is left out. ``all_terminals`` are every control and indicator the VI has,
    which the duplicate labels are found among."""
    ctx.import_panels(PANEL_NAME, at_module_level=True)
    defaults = default_functions(controls, ctx, target, lazy)
    keywords: list[tuple[str, ast.expr]] = []
    control_names = _first_by_label(controls)
    if control_names:
        keywords.append(
            (
                "controls",
                ast.Dict(
                    keys=[ast.Constant(value=label) for label in control_names],
                    values=[
                        ast.Tuple(
                            elts=[
                                ast.Constant(value=name),
                                defaults.entries.get(label, ast.Constant(value=None)),
                            ],
                            ctx=ast.Load(),
                        )
                        for label, name in control_names.items()
                    ],
                ),
            )
        )
    for entry in (_name_map("indicators", indicators), _name_map("passed", passed)):
        if entry is not None:
            keywords.append(entry)
    duplicates = duplicate_labels(all_terminals)
    if duplicates:
        keywords.append(
            (
                "duplicates",
                ast.List(
                    elts=[ast.Constant(value=label) for label in duplicates],
                    ctx=ast.Load(),
                ),
            )
        )
    assignment = ast.Assign(
        targets=[
            ast.Attribute(value=parse_expr(target), attr="panel", ctx=ast.Store())
        ],
        value=ast.Call(
            func=ast.Name(id=PANEL_NAME, ctx=ast.Load()),
            args=[parse_expr(target)],
            keywords=[ast.keyword(arg=name, value=value) for name, value in keywords],
        ),
    )
    return [*defaults.statements, assignment]


def function_panel_statements(
    vi_context: VIContext, ctx: CodeGenContext, target: str, lazy: bool
) -> list[ast.stmt]:
    """The ``panel`` of a VI's function: its controls by the parameters and its
    indicators by the fields of its result (``interface_names``), the controls
    and indicators off the connector pane without a name."""
    names = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, vi_context.outputs
    )
    controls: list[NamedTerminal] = [
        (t, names.parameters[t.id]) for t in vi_context.inputs
    ]
    controls += [(t, None) for t in vi_context.off_pane_inputs]
    indicators: list[NamedTerminal] = [
        (t, names.fields[t.id]) for t in vi_context.outputs
    ]
    indicators += [(t, None) for t in vi_context.off_pane_outputs]
    return panel_statements(
        ctx,
        target,
        controls,
        indicators,
        panel_terminals(vi_context),
        lazy,
    )

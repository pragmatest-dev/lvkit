"""The description of a VI's front panel that a generated function carries.

Each generated function gets ``function.panel = Panel(function, ...)`` right after
its ``def`` (after its class, for a method), naming its controls and indicators by
the one name table of the VI (``codegen/panel_names.py``) and giving the label
LabVIEW shows for each (``codegen/panels_module.py`` holds ``Panel``):

- ``controls``: the name of each control to its label. A control whose name is a
  parameter of the function is passed to it by a run; one off the connector pane
  is held only;
- ``defaults``: the name of each control that needs one to the function that makes
  its default. A control whose parameter has no default (a Required input) holds
  its default until a run sets it, so does a control that is not on the
  connector pane (no parameter, so a run cannot pass its value), and so does a
  control whose parameter default is ``None`` because its default is an array,
  cluster or class (the function body makes it): the function is a ``lambda``
  making the control's default (its saved default, else its type's, a class's
  default object), so each run gets a fresh list or object. Every other
  control's default is its parameter's own. Each indicator whose default is not
  ``None`` has one too (its saved default, else its type's): it holds that until
  a run writes it;
- ``indicators``: the name of each indicator to its label;
- ``results``: the name of each indicator on the connector pane to the field of the
  result that holds it;
- ``passed``: the name of a class output that is the object the method ran on to
  the parameter (``self``) whose value it shows, as a method whose class output is
  that object returns no field for it;
- ``clusters``: the name of each cluster control or indicator to the fields of
  its cluster, for the controls its Controls[] gives (``panel_clusters``);
- ``types`` and ``variants``: the type of each control and indicator that has a
  type-descriptor code, and the names of those that are Variants
  (``panel_types``);
- ``states``: the name of each control or indicator the file stores hidden, disabled
  or greyed out to its properties by member name (``{"x": {"visible": False}}``),
  what a panel starts the control in (``control_property_state.stored_states``);
- ``saved``: the name of each control or indicator that has a property the file saves
  and a running VI cannot change to those properties by member name
  (``{"e": {"strings": ["a", "b"]}, "b": {"mech_action": 4}}``: an Enum's items, a
  Boolean's Mechanical Action when it is not Switch When Pressed,
  ``control_property_state.saved_properties``), what a reference's ``get_property``
  reads;
- ``layout``: where the objects of the front panel are (panes, decorations, the
  rectangle of each object, a cluster's own objects), written by
  ``panel_layout.layout_expression``;
- ``elements``: the name of each array control or indicator to the element its Array
  Element property is a reference to, ``(class, front_panel_class, saved)``: the
  element's VI Server class name (None when it has none), its front-panel class
  when it has no VI Server class, and the properties it is saved with, as ``saved``
  above;
- ``classes``: the name of each control or indicator whose front-panel class has a
  VI Server class to that class's name (``FPTerminal.vi_server_class``), what its
  reference's ClassID reads (``control_classes``), and ``front_panel_classes``: the
  name of each control with no VI Server class to its front-panel class, which a
  ClassID read names when it raises NotImplementedError;
- ``order``: every name in the front panel's tabbing order (``FPTerminal.tab_index``,
  the object's place in the heap's ``ddoList``), left out when that is the controls
  and then the indicators in the order given above.

A label two objects share is told apart by tabbing order: a Ctrl Val method picks
"the first object whose label matches" in it, and the ``Panel`` holds the value of
each object by its own name.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import VIContext
from lvkit.models import FPTerminal, Terminal

from .ast_utils import interface_names, parameter_default_expr, parse_expr
from .control_property_state import (
    StateValue,
    element_saved_properties,
    saved_properties,
    state_expression,
    stored_states,
)
from .panel_clusters import cluster_specs
from .panel_layout import layout_expression
from .panel_names import panel_names
from .panel_types import panel_type_keywords
from .panels_module import PANEL_NAME
from .type_default import control_default
from .variants_module import imports_variants

if TYPE_CHECKING:
    from .context import CodeGenContext

# A terminal and the Python name that holds its value: the parameter of a control,
# the field of an indicator; None for a terminal that is not on the connector pane.
NamedTerminal = tuple[Terminal, str | None]


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


def _is_none(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Constant) and expression.value is None


def default_functions(
    controls: Sequence[NamedTerminal],
    names: dict[str, str],
    ctx: CodeGenContext,
    target: str,
    lazy: bool,
    indicators: Sequence[NamedTerminal] = (),
) -> DefaultFunctions:
    """The default function of each control whose parameter does not hold its
    default (a Required input, a control off the connector pane, a control whose
    ``def`` default is ``None`` because its default is an array, cluster or
    class that a ``def`` default cannot share), by its name in ``names``: a
    ``lambda`` making the default. A control whose type's default is ``None``
    has no entry.
    In a module that imports other generated modules inside its functions
    (``lazy``), a default that needs one is a ``def`` of ``target``'s name that
    imports it, as the function bodies do. A control whose default needs a class
    that is not loaded has no entry: the panel holds ``None`` for it (the class is
    reported where the VI uses it).
    Each of ``indicators`` has an entry too when its default is not ``None``: an
    indicator no run has written holds its default (the value the VI saved, else
    its type's default, a class's default object), as a front panel does."""
    defaults = DefaultFunctions()
    stem = target.replace(".", "_")
    entries = [(t, parameter, False) for t, parameter in controls]
    entries += [(t, None, True) for t, _ in indicators]
    for terminal, parameter, indicator in entries:
        on_def_default = parameter is not None and not is_required(terminal, "input")
        if on_def_default and not _is_none(parameter_default_expr(terminal)):
            continue
        module_imports, ctx.imports = ctx.imports, set()
        try:
            value = control_default(ctx, terminal)
        except ConversionGap:
            ctx.imports = module_imports
            continue
        added, ctx.imports = ctx.imports, module_imports
        if (on_def_default or indicator) and _is_none(value):
            continue
        needed = (
            {i for i in added if i.startswith("from .") and not imports_variants(i)}
            if lazy
            else set()
        )
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
            defaults.entries[names[terminal.id]] = ast.Name(id=function, ctx=ast.Load())
        else:
            defaults.entries[names[terminal.id]] = ast.Lambda(
                args=_no_parameters(), body=value
            )
    return defaults


def _tabbing_order(
    names: dict[str, str], objects: Sequence[NamedTerminal]
) -> list[str]:
    """The names of ``objects`` in the front panel's tabbing order; an object with
    no recorded place follows the ones that have one, in the order given."""

    def place(entry: tuple[int, NamedTerminal]) -> tuple[bool, int, int]:
        position, (terminal, _) = entry
        tab = terminal.tab_index if isinstance(terminal, FPTerminal) else None
        return (tab is None, tab or 0, position)

    ordered = sorted(enumerate(objects), key=place)
    return [names[terminal.id] for _, (terminal, _) in ordered]


def _name_map(
    keyword: str, entries: Iterable[tuple[str, str | int | None]]
) -> tuple[str, ast.Dict] | None:
    """``keyword={name: value}`` of the entries with a value (a label is "" for an
    object with none), None when there are none."""
    found = [(name, value) for name, value in entries if value is not None]
    if not found:
        return None
    return keyword, ast.Dict(
        keys=[ast.Constant(value=name) for name, _ in found],
        values=[ast.Constant(value=value) for _, value in found],
    )


def _member_dict(held: dict[str, StateValue]) -> ast.Dict:
    """``{member: value}`` as a dict expression."""
    return ast.Dict(
        keys=[ast.Constant(value=member) for member in held],
        values=[state_expression(v) for v in held.values()],
    )


def _property_dict(found: dict[str, dict[str, StateValue]]) -> ast.Dict:
    """``{name: {member: value}}`` as a dict expression."""
    return ast.Dict(
        keys=[ast.Constant(value=name) for name in found],
        values=[_member_dict(held) for held in found.values()],
    )


def _element_spec(terminal: FPTerminal) -> ast.Tuple:
    """``(class, front_panel_class, saved)`` of the element of the array control
    ``terminal``: its VI Server class name (None when the front-panel class has
    none), its front-panel class when it has none (what a ClassID read names), and
    the properties it is saved with (``element_saved_properties``)."""
    class_name = terminal.element_vi_server_class
    return ast.Tuple(
        elts=[
            ast.Constant(value=class_name),
            ast.Constant(
                value=terminal.element_control_type if class_name is None else None
            ),
            _member_dict(element_saved_properties(terminal)),
        ],
        ctx=ast.Load(),
    )


def panel_statements(
    ctx: CodeGenContext,
    target: str,
    names: dict[str, str],
    controls: Sequence[NamedTerminal],
    indicators: Sequence[NamedTerminal],
    lazy: bool,
    passed: Sequence[NamedTerminal] = (),
    vi_key: str | None = None,
) -> list[ast.stmt]:
    """``target.panel = Panel(target, controls=..., indicators=..., results=...,
    passed=..., defaults=..., clusters=..., types=..., variants=...)``, ``target``
    the expression of the function, preceded by the ``def`` of each default
    function a lazy module needs. A keyword with no entry is left out. ``names``
    is the VI's name table (``panel_names``); ``controls`` pair each control with
    the parameter that takes it (None off the connector pane), ``indicators`` each
    indicator with the field of the result that holds it (None off the pane) and
    ``passed`` each class output with the parameter holding the object it shows.
    ``vi_key`` is the VI whose front panel the ``layout`` keyword is written from
    (``panel_layout``)."""
    ctx.import_panels(PANEL_NAME, at_module_level=True)
    defaults = default_functions(controls, names, ctx, target, lazy, indicators)
    keywords: list[tuple[str, ast.expr]] = []
    for entry in (
        _name_map("controls", [(names[t.id], t.name or "") for t, _ in controls]),
        _name_map(
            "indicators",
            [(names[t.id], t.name or "") for t, _ in [*indicators, *passed]],
        ),
        _name_map("results", [(names[t.id], found) for t, found in indicators]),
        _name_map("passed", [(names[t.id], name) for t, name in passed]),
    ):
        if entry is not None:
            keywords.append(entry)
    clusters = {
        names[t.id]: specs
        for t, _ in [*controls, *indicators, *passed]
        if (specs := cluster_specs(ctx, t)) is not None
    }
    if clusters:
        keywords.append(
            (
                "clusters",
                ast.Dict(
                    keys=[ast.Constant(value=name) for name in clusters],
                    values=list(clusters.values()),
                ),
            )
        )
    typed = panel_type_keywords(
        [(names[t.id], t.lv_type) for t, _ in [*controls, *indicators, *passed]]
    )
    keywords.extend(typed.keywords)
    if typed.names:
        ctx.import_variant(*typed.names)
    if defaults.entries:
        keywords.append(
            (
                "defaults",
                ast.Dict(
                    keys=[ast.Constant(value=name) for name in defaults.entries],
                    values=list(defaults.entries.values()),
                ),
            )
        )
    objects = [*controls, *indicators, *passed]
    for keyword, found in (
        (
            "states",
            {
                names[t.id]: stored
                for t, _ in objects
                if isinstance(t, FPTerminal) and (stored := stored_states(t))
            },
        ),
        (
            "saved",
            {
                names[t.id]: held
                for t, _ in objects
                if isinstance(t, FPTerminal) and (held := saved_properties(t))
            },
        ),
    ):
        if found:
            keywords.append((keyword, _property_dict(found)))
    fp_objects = [t for t, _ in objects if isinstance(t, FPTerminal)]
    elements = {
        names[t.id]: _element_spec(t) for t in fp_objects if t.element_control_type
    }
    if elements:
        keywords.append(
            (
                "elements",
                ast.Dict(
                    keys=[ast.Constant(value=name) for name in elements],
                    values=list(elements.values()),
                ),
            )
        )
    for entry in (
        _name_map("classes", [(names[t.id], t.vi_server_class) for t in fp_objects]),
        _name_map(
            "front_panel_classes",
            [
                (names[t.id], t.control_type)
                for t in fp_objects
                if t.vi_server_class is None
            ],
        ),
        _name_map(
            "undecoded_strings",
            [(names[t.id], t.undecoded_strings or None) for t in fp_objects],
        ),
    ):
        if entry is not None:
            keywords.append(entry)
    layout = layout_expression(
        ctx.graph.get_vi_front_panel(vi_key)
        if ctx.graph is not None and vi_key is not None
        else None,
        {t.fp_dco_uid: names[t.id] for t in fp_objects if t.fp_dco_uid},
    )
    if layout is not None:
        keywords.append(("layout", layout))
    given = [names[t.id] for t, _ in objects]
    tabbing = _tabbing_order(names, objects)
    if tabbing != given:
        keywords.append(
            (
                "order",
                ast.List(
                    elts=[ast.Constant(value=name) for name in tabbing], ctx=ast.Load()
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
    and indicators off the connector pane without a field."""
    interface = interface_names(
        vi_context.inputs, vi_context.off_pane_inputs, vi_context.outputs
    )
    controls: list[NamedTerminal] = [
        (t, interface.parameters[t.id]) for t in vi_context.inputs
    ]
    controls += [(t, None) for t in vi_context.off_pane_inputs]
    indicators: list[NamedTerminal] = [
        (t, interface.fields[t.id]) for t in vi_context.outputs
    ]
    indicators += [(t, None) for t in vi_context.off_pane_outputs]
    return panel_statements(
        ctx,
        target,
        panel_names(vi_context),
        controls,
        indicators,
        lazy,
        vi_key=ctx.vi_name or vi_context.name,
    )

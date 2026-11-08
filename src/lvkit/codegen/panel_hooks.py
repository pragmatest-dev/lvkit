"""What generated code says about the live values of a running VI's front panel.

A VI whose diagram reads or writes its own front panel while it runs (a control
or indicator terminal inside a loop or Event Structure, a Local Variable of one
of its controls or indicators, a Value Property Node bound to one of its
controls: ``InMemoryVIGraph.touches_panel_while_running``) takes two keyword-only
parameters after its connector-pane ones::

    on_update: Callable[[AcquireValue, Any], None] | None = None
    on_read: Callable[[AcquireValue, Any], Any] | None = None

and calls ``on_update(AcquireValue.COUNT, value)`` after every write of one of
its indicators, and of one of its controls by a Local Variable. A caller that
passes nothing gets the VI exactly as it is without the parameters.
``on_read`` is called before a control that the diagram reads while running is
used: at the start of the diagram that holds a control terminal inside a loop
(``reads_in_frame``), at a Local Variable read of a control inside a loop (the
node generator of ``nodes/local_variable.py``), and once at entry for an
off-pane control the diagram reads (``entry_reads``)::

    if on_read is not None:
        stop = on_read(AcquireValue.STOP, stop)

The name is a member of ``AcquireValue``, the ``(str, Enum)`` the module of the
VI defines before its function (``PanelHooks.for_vi`` writes it): one member per
control and indicator, the member's value the name string (``"count"``), so a
panel imports the enum with the function and its logic and the panel share one
definition. The name is the control's parameter name or the indicator's result
field name, made distinct across the controls and indicators of the VI (a
control and an indicator may share a label). A cluster is passed whole. The
generated code uses plain ``if on_update is not None`` and imports nothing from
lvkit.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.models import FPTerminal, Terminal

from .ast_utils import (
    ON_READ,
    ON_UPDATE,
    class_style_name,
    distinct_names,
    interface_names,
    parse_expr,
    to_var_name,
    unique_names,
)
from .label_names import python_label

if TYPE_CHECKING:
    from .context import CodeGenContext


def _annotation(enum_name: str, result: str) -> str:
    return f"Callable[[{enum_name}, Any], {result}] | None"


@dataclass(frozen=True)
class PanelHooks:
    """The hooks of one VI: ``enum_name`` is the name of the enum its module
    defines, ``names`` maps the terminal id of each control and indicator of its
    front panel to the name the hooks pass for it and ``members`` to the
    enum's member that holds the name, ``indicators`` lists the terminal ids of
    the indicators, pane first, and ``controls`` those of the controls."""

    enum_name: str
    names: dict[str, str]
    members: dict[str, str]
    indicators: tuple[str, ...]
    controls: tuple[str, ...]

    @classmethod
    def for_vi(
        cls,
        ctx: CodeGenContext,
        vi_context: VIContext,
        graph: InMemoryVIGraph | None,
        vi_name: str,
        enum_name: str,
        taken: Iterable[str] = (),
        parameter_names: dict[str, str] | None = None,
    ) -> PanelHooks | None:
        """The hooks of ``vi_name``, None when its diagram does not touch its
        front panel while it runs. The enum is added to the module of ``ctx``
        under ``enum_name``, or a name made distinct from ``taken`` (the other
        names the module defines) and the names it already holds.
        ``parameter_names`` (terminal id to name) are the names a caller gave
        the parameters, when they are not the connector pane's own."""
        if graph is None or not graph.touches_panel_while_running(vi_name):
            return None
        interface = interface_names(
            vi_context.inputs, vi_context.off_pane_inputs, vi_context.outputs
        )
        controls = [*vi_context.inputs, *vi_context.off_pane_inputs]
        control_names = parameter_names or interface.parameters
        names = {c.id: control_names[c.id] for c in controls if c.id in control_names}
        named_controls = tuple(names)
        indicators = [*vi_context.outputs, *vi_context.off_pane_outputs]
        wanted = [_indicator_name(t, interface.fields) for t in indicators]
        for indicator, name in zip(
            indicators, distinct_names(names.values(), wanted), strict=True
        ):
            names[indicator.id] = name
        member_names = unique_names(
            [name.upper().strip("_") or "NAME" for name in names.values()]
        )
        members = dict(zip(names, member_names, strict=True))
        written = ctx.add_module_function(
            _enum_def(
                distinct_names([*taken, *ctx.helper_names.values()], [enum_name])[0],
                [(members[t], names[t]) for t in names],
            )
        )
        return cls(
            written, names, members, tuple(t.id for t in indicators), named_controls
        )

    def member(self, terminal_id: str) -> ast.expr:
        """``<Enum>.<MEMBER>``, the name passed for the terminal."""
        return ast.Attribute(
            value=ast.Name(id=self.enum_name, ctx=ast.Load()),
            attr=self.members[terminal_id],
            ctx=ast.Load(),
        )

    def arguments(self) -> tuple[list[ast.arg], list[ast.expr]]:
        """The keyword-only parameters of the VI, and their defaults."""
        annotations = {
            ON_UPDATE: _annotation(self.enum_name, "None"),
            ON_READ: _annotation(self.enum_name, "Any"),
        }
        return (
            [
                ast.arg(arg=name, annotation=parse_expr(annotation))
                for name, annotation in annotations.items()
            ],
            [ast.Constant(value=None) for _ in annotations],
        )


def function_enum_name(vi_name: str) -> str:
    """The wanted name of the enum of a VI's function, its name in class style
    (``acquire`` is ``AcquireValue``)."""
    return f"{class_style_name(vi_name)}Value"


def method_enum_name(class_name: str, method_name: str) -> str:
    """The wanted name of the enum of a class method, the class, then the method
    (``ScopeAcquireValue``)."""
    return f"{class_name}{class_style_name(method_name)}Value"


def _enum_def(name: str, members: list[tuple[str, str]]) -> ast.ClassDef:
    """``class <name>(str, Enum)`` with a member (name and value) for each of
    ``members``."""
    return ast.ClassDef(
        name=name,
        bases=[ast.Name(id="str", ctx=ast.Load()), ast.Name(id="Enum", ctx=ast.Load())],
        keywords=[],
        body=[
            ast.Assign(
                targets=[ast.Name(id=member, ctx=ast.Store())],
                value=ast.Constant(value=value),
            )
            for member, value in members
        ]
        or [ast.Pass()],
        decorator_list=[],
    )


def _indicator_name(indicator: Terminal, fields: dict[str, str]) -> str:
    """The result field name of a connector-pane indicator, the variable name of
    one that is not."""
    return fields.get(indicator.id) or to_var_name(
        python_label(indicator) or "indicator"
    )


def update_stmt(hooks: PanelHooks, terminal_id: str, variable: str) -> ast.stmt:
    """``if on_update is not None: on_update(<Enum>.<MEMBER>, <variable>)``, the
    member of the control or indicator ``terminal_id``."""
    return ast.If(
        test=ast.Compare(
            left=ast.Name(id=ON_UPDATE, ctx=ast.Load()),
            ops=[ast.IsNot()],
            comparators=[ast.Constant(value=None)],
        ),
        body=[
            ast.Expr(
                value=ast.Call(
                    func=ast.Name(id=ON_UPDATE, ctx=ast.Load()),
                    args=[
                        hooks.member(terminal_id),
                        ast.Name(id=variable, ctx=ast.Load()),
                    ],
                    keywords=[],
                )
            )
        ],
        orelse=[],
    )


def read_stmt(hooks: PanelHooks, terminal_id: str, variable: str) -> ast.stmt:
    """``if on_read is not None: <variable> = on_read(<Enum>.<MEMBER>,
    <variable>)``, the member of the control ``terminal_id``."""
    return ast.If(
        test=ast.Compare(
            left=ast.Name(id=ON_READ, ctx=ast.Load()),
            ops=[ast.IsNot()],
            comparators=[ast.Constant(value=None)],
        ),
        body=[
            ast.Assign(
                targets=[ast.Name(id=variable, ctx=ast.Store())],
                value=ast.Call(
                    func=ast.Name(id=ON_READ, ctx=ast.Load()),
                    args=[
                        hooks.member(terminal_id),
                        ast.Name(id=variable, ctx=ast.Load()),
                    ],
                    keywords=[],
                ),
            )
        ],
        orelse=[],
    )


def reads_in_frame(
    ctx: CodeGenContext, frame: tuple[str, str | int | None]
) -> list[ast.stmt]:
    """The reads that start the diagram of the structure frame ``frame``: each
    control whose terminal sits in that frame inside a loop or Event Structure
    is read again at every pass."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    if hooks is None or graph is None:
        return []
    statements: list[ast.stmt] = []
    for terminal_id in hooks.controls:
        terminal = graph.get_terminal(terminal_id)
        if (
            isinstance(terminal, FPTerminal)
            and graph.front_panel_loop(terminal_id) is not None
            and (terminal.parent, terminal.frame) == frame
        ):
            variable = ctx.resolve(terminal_id)
            if variable is not None:
                statements.append(read_stmt(hooks, terminal_id, variable))
    return statements


def entry_reads(ctx: CodeGenContext, off_pane: list[Terminal]) -> list[ast.stmt]:
    """The reads at entry of the off-pane controls ``off_pane`` that the
    diagram reads (a wire leaves the terminal), after their saved defaults."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    if hooks is None or graph is None:
        return []
    statements: list[ast.stmt] = []
    for control in off_pane:
        if control.id in hooks.names and graph.wire_readers(control.id):
            variable = ctx.resolve(control.id) or hooks.names[control.id]
            statements.append(read_stmt(hooks, control.id, variable))
    return statements

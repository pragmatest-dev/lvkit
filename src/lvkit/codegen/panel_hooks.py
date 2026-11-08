"""What generated code says about the live values of a running VI's front panel.

A VI whose diagram reads or writes its own front panel while it runs (a control
or indicator terminal inside a loop or Event Structure, a Local Variable of one
of its controls or indicators, a Value Property Node bound to one of its
controls: ``InMemoryVIGraph.touches_panel_while_running``) takes two keyword-only
parameters after its connector-pane ones::

    on_update: Callable[[str, Any], None] | None = None
    on_read: Callable[[str, Any], Any] | None = None

and calls ``on_update("<name>", value)`` after every write of one of its
indicators. A caller that passes nothing gets the VI exactly as it is without the
parameters. ``on_update`` is used from this module; ``on_read`` is called
before a control that the diagram reads while running is used: at the start of
the diagram that holds a control terminal inside a loop (``reads_in_frame``), and
once at entry for an off-pane control the diagram reads (``entry_reads``)::

    if on_read is not None:
        stop = on_read("stop", stop)

The name passed is the control's parameter name or the indicator's result field
name, made distinct across the controls and indicators of the VI (a control and
an indicator may share a label). A cluster is passed whole. The generated code
uses plain ``if on_update is not None`` and imports nothing from lvkit.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.models import FPTerminal, Terminal

from .ast_utils import (
    ON_READ,
    ON_UPDATE,
    distinct_names,
    interface_names,
    parse_expr,
    to_var_name,
)
from .label_names import python_label

if TYPE_CHECKING:
    from .context import CodeGenContext

_ANNOTATIONS = {
    ON_UPDATE: "Callable[[str, Any], None] | None",
    ON_READ: "Callable[[str, Any], Any] | None",
}


@dataclass(frozen=True)
class PanelHooks:
    """The hooks of one VI: ``names`` maps the terminal id of each control and
    indicator of its front panel to the name the hooks pass for it,
    ``indicators`` lists the terminal ids of the indicators, pane first, and
    ``controls`` those of the controls."""

    names: dict[str, str]
    indicators: tuple[str, ...]
    controls: tuple[str, ...]

    @classmethod
    def for_vi(
        cls,
        vi_context: VIContext,
        graph: InMemoryVIGraph | None,
        vi_name: str,
        parameter_names: dict[str, str] | None = None,
    ) -> PanelHooks | None:
        """The hooks of ``vi_name``, None when its diagram does not touch its
        front panel while it runs. ``parameter_names`` (terminal id to name) are
        the names a caller gave the parameters, when they are not the
        connector pane's own."""
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
        return cls(names, tuple(t.id for t in indicators), named_controls)


def _indicator_name(indicator: Terminal, fields: dict[str, str]) -> str:
    """The result field name of a connector-pane indicator, the variable name of
    one that is not."""
    return fields.get(indicator.id) or to_var_name(
        python_label(indicator) or "indicator"
    )


def hook_arguments() -> tuple[list[ast.arg], list[ast.expr]]:
    """The keyword-only parameters of a hooked VI, and their defaults."""
    return (
        [
            ast.arg(arg=name, annotation=parse_expr(annotation))
            for name, annotation in _ANNOTATIONS.items()
        ],
        [ast.Constant(value=None) for _ in _ANNOTATIONS],
    )


def update_stmt(name: str, variable: str) -> ast.stmt:
    """``if on_update is not None: on_update("<name>", <variable>)``."""
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
                        ast.Constant(value=name),
                        ast.Name(id=variable, ctx=ast.Load()),
                    ],
                    keywords=[],
                )
            )
        ],
        orelse=[],
    )


def read_stmt(name: str, variable: str) -> ast.stmt:
    """``if on_read is not None: <variable> = on_read("<name>", <variable>)``."""
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
                        ast.Constant(value=name),
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
                statements.append(read_stmt(hooks.names[terminal_id], variable))
    return statements


def entry_reads(ctx: CodeGenContext, off_pane: list[Terminal]) -> list[ast.stmt]:
    """The reads at entry of the off-pane controls ``off_pane`` that the
    diagram reads (a wire leaves the terminal), after their saved defaults."""
    hooks, graph = ctx.panel_hooks, ctx.graph
    if hooks is None or graph is None:
        return []
    return [
        read_stmt(hooks.names[c.id], hooks.names[c.id])
        for c in off_pane
        if c.id in hooks.names and graph.wire_readers(c.id)
    ]

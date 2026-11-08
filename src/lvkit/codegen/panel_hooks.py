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
parameters. ``on_update`` is used from this module; ``on_read`` is declared here
and its read lines come with the control reads.

The name passed is the control's parameter name or the indicator's result field
name, made distinct across the controls and indicators of the VI (a control and
an indicator may share a label). A cluster is passed whole. The generated code
uses plain ``if on_update is not None`` and imports nothing from lvkit.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.models import Terminal

from .ast_utils import (
    ON_READ,
    ON_UPDATE,
    distinct_names,
    interface_names,
    parse_expr,
    to_var_name,
)
from .label_names import python_label

_ANNOTATIONS = {
    ON_UPDATE: "Callable[[str, Any], None] | None",
    ON_READ: "Callable[[str, Any], Any] | None",
}


@dataclass(frozen=True)
class PanelHooks:
    """The hooks of one VI: ``names`` maps the terminal id of each control and
    indicator of its front panel to the name the hooks pass for it, and
    ``indicators`` lists the terminal ids of the indicators, pane first."""

    names: dict[str, str]
    indicators: tuple[str, ...]

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
        indicators = [*vi_context.outputs, *vi_context.off_pane_outputs]
        wanted = [_indicator_name(t, interface.fields) for t in indicators]
        for indicator, name in zip(
            indicators, distinct_names(names.values(), wanted), strict=True
        ):
            names[indicator.id] = name
        return cls(names, tuple(t.id for t in indicators))


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

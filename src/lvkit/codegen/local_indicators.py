"""An indicator a Local Variable reads, or a connector-pane indicator written
inside a structure frame, is a variable of the function.

LabVIEW's indicator holds a value: its default until something writes it, then
what was last written. So the variable is set to the indicator's default when
the function starts, every wire that writes the indicator (directly or through a
Local Variable write) assigns it, and a Local Variable read reads it. A read in a
loop therefore sees what an earlier iteration wrote.

A connector-pane indicator that a call may finish without writing (its writer is
in a case frame that does not run, or a loop that runs zero times) returns its
default, every call: LabVIEW clears a flag before the VI runs and writes the
default when the indicator was not written (NI's Greg McKaskle on NI's forum,
https://forums.ni.com/t5/LabVIEW/Local-variable-behaviour-different-with-panel-open/td-p/81009).
Its variable, set to the default when the function starts, gives that."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode

from .ast_utils import build_assign, control_default_expr

if TYPE_CHECKING:
    from .context import CodeGenContext


@dataclass
class LocalIndicators:
    """The indicators of one VI held in variables (see the module docstring),
    with their variables."""

    variables: dict[str, str] = field(default_factory=dict)

    @classmethod
    def declare(
        cls, ctx: CodeGenContext, vi_name: str
    ) -> tuple[LocalIndicators, list[ast.stmt]]:
        """The indicators of ``vi_name`` read by a Local Variable or written in
        a structure frame, each bound to a variable, and the statements that set
        them to their defaults."""
        found = LocalIndicators()
        statements: list[ast.stmt] = []
        if ctx.graph is None:
            return found, statements
        held = {
            **ctx.graph.indicator_local_reads(vi_name),
            **ctx.graph.indicators_written_in_a_frame(vi_name),
        }
        for terminal_id, control in sorted(held.items()):
            variable = ctx.make_output_var(
                control.name or "indicator", ctx.graph.terminal_node_id(terminal_id)
            )
            ctx.bind(terminal_id, variable)
            found.variables[terminal_id] = variable
            statements.append(build_assign(variable, control_default_expr(control)))
            # A control or constant wired straight to the indicator has no node
            # to assign it after: it sets the variable here.
            for source in ctx.graph.indicator_sources(terminal_id):
                value = ctx.resolve(source, allow_none=True)
                if value is not None and value != variable:
                    statements.append(build_assign(variable, _expression(value)))
        return found, statements

    def assignments_after(
        self, node: AnyGraphNode, ctx: CodeGenContext
    ) -> list[ast.stmt]:
        """The assignments of the indicators ``node``'s outputs write, from the
        values the node produced."""
        if not self.variables or ctx.graph is None:
            return []
        tracked = set(self.variables)
        statements: list[ast.stmt] = []
        for term in node.terminals:
            value = ctx.resolve(term.id) if term.direction == "output" else None
            if value is None:
                continue
            for indicator in ctx.graph.indicators_written_by(term.id, tracked):
                if value == self.variables[indicator]:
                    continue
                statements.append(
                    build_assign(self.variables[indicator], _expression(value))
                )
        return statements


def _expression(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body

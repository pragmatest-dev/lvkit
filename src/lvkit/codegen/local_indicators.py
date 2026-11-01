"""An indicator a Local Variable reads is a variable of the function.

LabVIEW's indicator holds a value: its default until something writes it, then
what was last written. So the variable is set to the indicator's default when
the function starts, every wire that writes the indicator (directly or through a
Local Variable write) assigns it, and a Local Variable read reads it. A read in a
loop therefore sees what an earlier iteration wrote."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode

from .ast_utils import build_assign, param_default_expr

if TYPE_CHECKING:
    from .context import CodeGenContext


@dataclass
class LocalIndicators:
    """The indicators of one VI that a Local Variable reads, with their variables."""

    variables: dict[str, str] = field(default_factory=dict)

    @classmethod
    def declare(
        cls, ctx: CodeGenContext, vi_name: str
    ) -> tuple[LocalIndicators, list[ast.stmt]]:
        """The indicators of ``vi_name`` read by a Local Variable, each bound to a
        variable, and the statements that set them to their defaults."""
        found = LocalIndicators()
        statements: list[ast.stmt] = []
        if ctx.graph is None:
            return found, statements
        for terminal_id, control in sorted(
            ctx.graph.indicator_local_reads(vi_name).items()
        ):
            variable = ctx.make_output_var(
                control.name or "indicator", ctx.graph.terminal_node_id(terminal_id)
            )
            ctx.bind(terminal_id, variable)
            found.variables[terminal_id] = variable
            statements.append(build_assign(variable, param_default_expr(control)))
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

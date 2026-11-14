"""An indicator a Local Variable reads, or a connector-pane indicator written
inside a structure frame, is a variable of the function; so is an indicator a
bound Property Node's ``Value`` row reads or writes.

LabVIEW's indicator holds a value: every wire that writes the indicator
(directly or through a Local Variable write) assigns its variable, and a Local
Variable read reads it, so a read in a loop sees what an earlier iteration
wrote.

An indicator keeps its value from one call of its VI to the next, unless the VI
has "Clear indicators when called" (``ExecutionProps.clear_indicators_when_called``),
which starts it at its default. A connector-pane indicator that a call finishes
without writing gets its default (LabVIEW clears a flag before the VI runs and
writes the default when the indicator was not written: NI's Greg McKaskle on NI's
forum,
https://forums.ni.com/t5/LabVIEW/Local-variable-behaviour-different-with-panel-open/td-p/81009).

So an indicator a Local Variable reads keeps its value in a module variable
(``CodeGenContext.add_state_global``): the function starts from it and stores the
indicator back into it at the end. In a VI that clears its indicators it starts
from its default, like an indicator no Local Variable reads. When something
outside every frame writes a connector-pane indicator, every call writes it;
otherwise a written flag, set by every write, gives it its default at the end
when the call did not write it. An indicator no Local Variable reads is never
read before this call writes it: its variable starts at its default, which is
what it returns when the call does not write it."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode
from lvkit.models import FPTerminal

from .ast_utils import build_assign, node_owned_name
from .panel_hooks import PanelHooks, write_stmt
from .references import is_walked_read
from .type_default import control_default, default_if_unset, holds_class, state_seed
from .variant_values import wrap_if_coerced

if TYPE_CHECKING:
    from .context import CodeGenContext


@dataclass
class _Kept:
    """An indicator whose value the module keeps between calls."""

    variable: str
    module_name: str
    control: FPTerminal
    written_flag: str | None


@dataclass
class LocalIndicators:
    """The indicators of one VI held in variables (see the module docstring),
    with their variables."""

    variables: dict[str, str] = field(default_factory=dict)
    kept: dict[str, _Kept] = field(default_factory=dict)
    # The hooks of the VI (``panel_hooks``), whose ``live_io.write`` is called after
    # each write of an indicator; None in a VI that has none.
    hooks: PanelHooks | None = None

    @classmethod
    def declare(
        cls, ctx: CodeGenContext, vi_name: str
    ) -> tuple[LocalIndicators, list[ast.stmt]]:
        """The indicators of ``vi_name`` read by a Local Variable or written in
        a structure frame (every indicator, in a VI that has the hooks of
        ``panel_hooks``), each bound to a variable, and the statements that
        start them."""
        found = LocalIndicators()
        statements: list[ast.stmt] = []
        if ctx.graph is None:
            return found, statements
        value_reads, value_writes = ctx.graph.indicators_of_value_rows(vi_name)
        read = {**ctx.graph.indicator_local_reads(vi_name), **value_reads}
        held = {
            **read,
            **ctx.graph.indicators_written_in_a_frame(vi_name),
            **value_writes,
        }
        if ctx.panel_hooks is not None:
            for terminal_id in ctx.panel_hooks.indicators:
                control = ctx.graph.get_terminal(terminal_id)
                if isinstance(control, FPTerminal):
                    held[terminal_id] = control
            found.hooks = ctx.panel_hooks
        clears = ctx.graph.get_vi_properties(
            vi_name
        ).execution.clear_indicators_when_called
        for terminal_id, control in sorted(held.items()):
            owner = ctx.graph.terminal_node_id(terminal_id)
            variable = ctx.make_output_var(control.name or "indicator", owner)
            ctx.bind(terminal_id, variable)
            found.variables[terminal_id] = variable
            if terminal_id in read and not clears:
                kept = found._keep(ctx, terminal_id, control, variable)
                statements.append(ast.Global(names=[kept.module_name]))
                statements.extend(
                    default_if_unset(
                        kept.module_name,
                        control.lv_type,
                        ctx,
                        f"the indicator {control.name or variable}",
                    )
                )
                start = ast.Name(id=kept.module_name, ctx=ast.Load())
                statements.append(build_assign(variable, start))
                if kept.written_flag is not None:
                    statements.append(
                        build_assign(kept.written_flag, ast.Constant(value=False))
                    )
            else:
                statements.append(build_assign(variable, control_default(ctx, control)))
            # A control or constant on the VI's own diagram wired straight to
            # the indicator has no node to assign it after: it sets the variable
            # here. A frame's inside face writes it in the frame, and a Local
            # Variable read in the walk after its node.
            for source in ctx.graph.indicator_sources(terminal_id):
                if ctx.graph.inside_a_frame(source) or is_walked_read(source, ctx):
                    continue
                value = ctx.resolve(source, allow_none=True)
                if value is not None and value != variable:
                    value = wrap_if_coerced(ctx, terminal_id, value, source)
                    statements.extend(found._write(terminal_id, _expression(value)))
        return found, statements

    def _keep(
        self,
        ctx: CodeGenContext,
        terminal_id: str,
        control: FPTerminal,
        variable: str,
    ) -> _Kept:
        assert ctx.graph is not None
        module_name = node_owned_name("_lv_indicator", ctx.function_name, terminal_id)
        seed = (
            state_seed(control.lv_type)
            if holds_class(control.lv_type)
            else control_default(ctx, control)
        )
        ctx.add_state_global(module_name, seed, owner=terminal_id)
        flag = (
            ctx.make_output_var(f"{variable}_written", terminal_id)
            if control.is_public
            and not ctx.graph.indicator_written_on_vi_diagram(terminal_id)
            else None
        )
        kept = _Kept(variable, module_name, control, flag)
        self.kept[terminal_id] = kept
        return kept

    def write(self, terminal_id: str, value: str) -> list[ast.stmt]:
        """The statements that write the expression ``value`` to the held
        indicator ``terminal_id``."""
        if value == self.variables[terminal_id]:
            return self._write(terminal_id, None)
        return self._write(terminal_id, _expression(value))

    def _write(self, terminal_id: str, value: ast.expr | None) -> list[ast.stmt]:
        """The statements that write ``value`` to the indicator (``None``: its
        variable already holds the value)."""
        statements: list[ast.stmt] = []
        if value is not None:
            statements.append(build_assign(self.variables[terminal_id], value))
        kept = self.kept.get(terminal_id)
        if kept is not None and kept.written_flag is not None:
            statements.append(build_assign(kept.written_flag, ast.Constant(True)))
        if self.hooks is not None and terminal_id in self.hooks.indicators:
            statements.append(
                write_stmt(self.hooks, terminal_id, self.variables[terminal_id])
            )
        return statements

    def assignments_after(
        self, node: AnyGraphNode, ctx: CodeGenContext
    ) -> list[ast.stmt]:
        """The assignments of the indicators ``node``'s outputs write, from the
        values the node produced. An inside face of a structure writes inside
        its frame (``writes_in_frame``), not after the structure."""
        if not self.variables or ctx.graph is None:
            return []
        return self._writes_from(
            [
                t.id
                for t in node.terminals
                if t.direction == "output" and ctx.graph.inside_face(t.id) is None
            ],
            ctx,
        )

    def writes_in_frame(
        self, frame: tuple[str, str | int | None], ctx: CodeGenContext
    ) -> list[ast.stmt]:
        """The assignments that start the frame ``frame`` (a structure and its
        frame key): the indicators an inside face of that frame, or a constant
        in it, writes (``InMemoryVIGraph.written_in_frame``)."""
        if not self.variables or ctx.graph is None:
            return []
        graph = ctx.graph
        sources = sorted(
            {
                source
                for indicator in self.variables
                for source in graph.indicator_sources(indicator)
                if graph.written_in_frame(source, frame)
            }
        )
        return self._writes_from(sources, ctx)

    def _writes_from(
        self, terminal_ids: list[str], ctx: CodeGenContext
    ) -> list[ast.stmt]:
        """The assignments of the indicators the output terminals
        ``terminal_ids`` write."""
        assert ctx.graph is not None
        tracked = set(self.variables)
        statements: list[ast.stmt] = []
        for terminal_id in terminal_ids:
            value = ctx.resolve(terminal_id)
            if value is None:
                continue
            for indicator in ctx.graph.indicators_written_by(terminal_id, tracked):
                written = (
                    None
                    if value == self.variables[indicator]
                    else _expression(
                        wrap_if_coerced(ctx, indicator, value, terminal_id)
                    )
                )
                statements.extend(self._write(indicator, written))
        return statements

    def statements_at_end(self, ctx: CodeGenContext) -> list[ast.stmt]:
        """The statements that end the call: an indicator the call did not
        write takes its default, and every kept indicator is stored back."""
        statements: list[ast.stmt] = []
        for kept in (self.kept[t] for t in sorted(self.kept)):
            if kept.written_flag is not None:
                statements.append(
                    ast.If(
                        test=ast.UnaryOp(
                            op=ast.Not(),
                            operand=ast.Name(id=kept.written_flag, ctx=ast.Load()),
                        ),
                        body=[
                            build_assign(
                                kept.variable, control_default(ctx, kept.control)
                            )
                        ],
                        orelse=[],
                    )
                )
            statements.append(
                build_assign(
                    kept.module_name, ast.Name(id=kept.variable, ctx=ast.Load())
                )
            )
        return statements


def _expression(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body

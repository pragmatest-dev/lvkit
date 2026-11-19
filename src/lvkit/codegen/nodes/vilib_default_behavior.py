"""What an unwired vi.lib input holds when NI documents a behaviour, not a value.

The behaviours a vi.lib catalog entry's terminal records as ``default_behavior``
and the Python each is written as. A behaviour is a statement that makes the
input's value, so the node's output that passes the input through (a task out)
is the same object.

* ``new task``: DAQmx Create Virtual Channel, "task in specifies the task to
  which to add the virtual channels this VI creates. If you do not specify a
  task, NI-DAQmx creates a task for you and adds the virtual channels this VI
  creates to that task" (NI-DAQmx LabVIEW API Reference, DAQmx Create Channel
  (AI-Voltage-Basic) VI). The Python equivalent is a new ``nidaqmx.Task``.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import VINode
from lvkit.terminal_default import DefaultBehavior

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext

_EXPRESSIONS: dict[DefaultBehavior, str] = {
    DefaultBehavior.NEW_TASK: "nidaqmx.Task()",
    # The Picture VIs (``picture_values``): the empty picture is None, the pen
    # is (width, style) with a width of one pixel and the solid style (style 0
    # is NI's "(default)"; NI states no default width, so one pixel).
    DefaultBehavior.EMPTY_PICTURE: "None",
    DefaultBehavior.DEFAULT_PEN: "(1, 0)",
    DefaultBehavior.ZERO_POINT: "(0, 0)",
    DefaultBehavior.WHOLE_IMAGE: "None",
    DefaultBehavior.EMPTY_ARRAY: "[]",
    DefaultBehavior.IGNORED: "None",
}


def behavior_variable(
    behavior: DefaultBehavior,
    ctx: CodeGenContext,
    node: VINode,
    name: str,
    statements: list[ast.stmt],
) -> str | None:
    """The variable holding what ``behavior`` makes, its assignment added to
    ``statements``; None when no expression is written for ``behavior``."""
    expression = _EXPRESSIONS.get(behavior)
    if expression is None:
        return None
    variable = ctx.make_output_var(name, node.id)
    statements.append(build_assign(variable, parse_expr(expression)))
    return variable

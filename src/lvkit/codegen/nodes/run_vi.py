"""Run VI, the method of a VI reference (VI class, method code 1003).

Run VI runs the VI with the current values of its front-panel controls, passing
no data through parameters (NI: properties-and-methods/vi-server/vi/run.html).
Every VI has a front panel (``function.panel``, ``codegen/panels_module.py``) that
holds those values, set anywhere a diagram holds a reference to the VI, so Run VI
is always ``panel_of(reference).run(wait, submit)``:

- Wait Until Done is the value wired to it, TRUE (its default) when unwired, and
  with TRUE the run is ``.run(True)``;
- Wait Until Done FALSE, or computed when the diagram runs, also passes the
  ``submit`` of the thread pool Start Asynchronous Call uses
  (``vi_reference.pool_submit``): the run returns once the panel is Running and
  goes on, the VI started on that pool;
- Auto Dispose Ref only says who closes the reference: no Python meaning;
- a reference that holds no VI, or a VI that is already running, is the Invoke
  Node's own error (``ctrl_val.report_panel_error``): error in and error out are
  the Invoke Node's own, so the run happens only when no error came in.

A gap where the reference's VI is known when the code is written and is not
loaded with this VI."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_CLASS, ReferencedVI, VIReferenceUnresolved
from lvkit.models import Terminal

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..panels_module import PANEL_CALL_IMPORTS, PANEL_OF_NAME
from . import ctrl_val
from .vi_reference import pool_submit

RUN_VI_METHOD_CODE = 1003

# The Invoke Node's VI reference in and out.
_REFERENCE_IN = 0
_REFERENCE_OUT = 1
# Wait Until Done: the first parameter row, NI's order (Wait Until Done, then
# Auto Dispose Ref); in the icon editor's Call Keep ApplyLib in Memory.vi the
# constants LabVIEW created from these terminals, labelled "Wait Until Done" and
# "Auto Dispose Reference", are wired to terminals 6 and 8.
_WAIT_UNTIL_DONE = 6


def is_run_vi(node: PrimitiveNode) -> bool:
    """Whether ``node`` is an Invoke Node calling Run VI on a VI reference."""
    return node.object_name == VI_CLASS and node.method_code == RUN_VI_METHOD_CODE


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """``panel_of(reference).run(wait, submit)``: the VI runs with the values its
    front panel holds."""
    assert ctx.graph is not None
    reference_in = _input(node, _REFERENCE_IN)
    if reference_in is None or not ctx.is_wired(reference_in.id):
        raise ConversionGap("Run VI: the VI reference input is not wired.", node)
    try:
        target: ReferencedVI | None = ctx.graph.referenced_vi(reference_in.id)
    except VIReferenceUnresolved:
        target = None
    if target is not None and not ctx.graph.is_loaded_vi(target.key):
        raise ConversionGap(
            f"Run VI: the VI {target.name!r} is not loaded with this VI.", node
        )
    reference = ctrl_val.reference_value(node, ctx)
    wait_in = _input(node, _WAIT_UNTIL_DONE)
    wait = (
        ctx.wired_input(wait_in.id, node)
        if wait_in is not None and ctx.is_wired(wait_in.id)
        else None
    ) or "True"
    reference_out = ctrl_val.terminal_at(node, "output", _REFERENCE_OUT)
    bindings = {reference_out.id: reference} if reference_out is not None else {}
    ctx.import_panels(PANEL_CALL_IMPORTS)
    arguments = wait if wait == "True" else f"{wait}, {pool_submit(ctx)}"
    run = ast.Expr(value=parse_expr(f"{PANEL_OF_NAME}({reference}).run({arguments})"))
    return ctrl_val.report_panel_error(node, ctx, [run], bindings)


def _input(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )

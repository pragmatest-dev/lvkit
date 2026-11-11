"""Run VI, the method of a VI reference (VI class, method code 1003).

Run VI runs the VI with the current values of its front-panel controls, passing
no data through parameters (NI: properties-and-methods/vi-server/vi/run.html). A
VI is a Python function, so Run VI on a reference whose VI is known when the code
is generated (``InMemoryVIGraph.referenced_vi``), with nothing set on that
reference, is a call of that function with no arguments:

- Wait Until Done TRUE (its default, when unwired) is ``function()``;
- Wait Until Done FALSE starts the function and goes on, on the thread pool
  Start Asynchronous Call uses (``vi_reference.submit_to_pool``);
- Auto Dispose Ref only says who closes the reference: no Python meaning;
- error in and error out are the Invoke Node's own: the run happens only when
  no error came in, and error out is error in (``error_flow.guard``).

A gap where a call with no arguments cannot be that run: Wait Until Done computed
when the diagram runs; a VI with a Required input; a dynamic dispatch member VI;
and a diagram that writes a property of the same VI reference or calls another
method on it (Set Control Value, say), which the run would use.

Run VI on a reference the code only knows when it runs, or on a VI whose front
panel the diagram reaches through the reference (Ctrl Val.Set, Get and Get All,
or a reference passed to a subVI), is ``FrontPanel.of(reference).run(wait)``
(``ctrl_val``): the front panel holds the values the run uses."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import ConstantNode, PrimitiveNode, VINode
from lvkit.graph.vi_reference import VI_CLASS, ReferencedVI, VIReferenceUnresolved
from lvkit.models import Terminal

from ..ast_utils import build_assign, parse_expr
from ..class_model import MethodKind, member_shape
from ..context import CodeGenContext, _format_constant
from ..fragment import CodeFragment
from ..front_panels_module import FRONT_PANEL_NAME
from . import ctrl_val
from .vi_reference import submit_to_pool

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
    """The run of the function of the VI the reference holds: a call of it, waited
    for or started on the thread pool, or a run of its front panel."""
    assert ctx.graph is not None
    reference = _input(node, _REFERENCE_IN)
    if reference is None or not ctx.is_wired(reference.id):
        raise ConversionGap("Run VI: the VI reference input is not wired.", node)
    try:
        target: ReferencedVI | None = ctx.graph.referenced_vi(reference.id)
    except VIReferenceUnresolved:
        target = None
    if target is None or _front_panel_used(node, ctx, target):
        return _run_front_panel(node, ctx, target)
    wait = _wait_until_done(node, ctx)
    _check_runs_with_defaults(node, ctx, target)
    function = ctx.vi_reference_expression(target.name, target.key)

    bindings: dict[str, str] = {}
    reference_out = next(
        (
            t
            for t in node.terminals
            if t.direction == "output" and t.index == _REFERENCE_OUT
        ),
        None,
    )
    reference_value = ctx.wired_input(reference.id, node)
    if reference_out is not None and reference_value is not None:
        bindings[reference_out.id] = reference_value
    run = (
        ast.Call(func=parse_expr(function), args=[], keywords=[])
        if wait
        else submit_to_pool(ctx, function, [])
    )
    return CodeFragment(statements=[ast.Expr(value=run)], bindings=bindings)


def _run_front_panel(
    node: PrimitiveNode, ctx: CodeGenContext, target: ReferencedVI | None
) -> CodeFragment:
    """``FrontPanel.of(reference).run(wait)``: the VI runs with the values its
    front panel holds. Wait Until Done is the value wired to it, TRUE when
    unwired."""
    assert ctx.graph is not None
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
    panel = ctx.make_output_var("front_panel", node.id)
    reference_out = ctrl_val.terminal_at(node, "output", _REFERENCE_OUT)
    bindings = {reference_out.id: reference} if reference_out is not None else {}
    ctx.import_front_panel()
    return ctrl_val.report_panel_error(
        node,
        ctx,
        [build_assign(panel, parse_expr(f"{FRONT_PANEL_NAME}.of({reference})"))],
        bindings,
        after=[ast.Expr(value=parse_expr(f"{panel}.run({wait})"))],
    )


def _front_panel_used(
    node: PrimitiveNode, ctx: CodeGenContext, target: ReferencedVI
) -> bool:
    """Whether another node of this diagram reaches the front panel of the VI
    ``target`` holds through a reference to it: Ctrl Val.Set, Get or Get All, or a
    subVI the reference is passed to, which may set a value on it."""
    assert ctx.graph is not None and ctx.vi_name is not None
    for other in ctx.graph.iter_nodes(ctx.vi_name):
        if other is node:
            continue
        if isinstance(other, PrimitiveNode) and ctrl_val.is_ctrl_val(other):
            terminals = [ctrl_val.terminal_at(other, "input", _REFERENCE_IN)]
        elif isinstance(other, VINode):
            terminals = [
                t
                for t in other.terminals
                if t.direction == "input"
                and t.lv_type is not None
                and t.lv_type.underlying_type == "Refnum"
            ]
        else:
            continue
        for terminal in terminals:
            if terminal is not None and _holds(ctx, terminal, target):
                return True
    return False


def _holds(ctx: CodeGenContext, terminal: Terminal, target: ReferencedVI) -> bool:
    assert ctx.graph is not None
    if not ctx.is_wired(terminal.id):
        return False
    try:
        return ctx.graph.referenced_vi(terminal.id).key == target.key
    except VIReferenceUnresolved:
        return False


def _input(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == index),
        None,
    )


def _wait_until_done(node: PrimitiveNode, ctx: CodeGenContext) -> bool:
    """Wait Until Done: TRUE when unwired; the value of the constant wired to
    it; a gap when it is computed when the diagram runs."""
    assert ctx.graph is not None
    terminal = _input(node, _WAIT_UNTIL_DONE)
    if terminal is None or not ctx.is_wired(terminal.id):
        return True
    values: set[object] = set()
    for origin, _ in ctx.graph.wire_origins(terminal.id):
        if not isinstance(origin, ConstantNode):
            raise ConversionGap(
                "Run VI: Wait Until Done is computed when the diagram runs, so "
                "whether to wait for the VI is not known when the code is written.",
                node,
            )
        values.add(ast.literal_eval(_format_constant(origin)))
    if len(values) != 1 or not isinstance(next(iter(values)), bool):
        raise ConversionGap(
            "Run VI: the frames of a case structure give Wait Until Done different "
            "values, so whether to wait for the VI is only known when it runs.",
            node,
        )
    return bool(values.pop())


def _check_runs_with_defaults(
    node: PrimitiveNode, ctx: CodeGenContext, target: ReferencedVI
) -> None:
    """A call with no arguments is the run only when the VI is loaded, needs no
    input, is not dynamic dispatch and the diagram sets nothing on its
    reference."""
    assert ctx.graph is not None
    if not ctx.graph.is_loaded_vi(target.key):
        raise ConversionGap(
            f"Run VI: the VI {target.name!r} is not loaded with this VI.", node
        )
    shape = member_shape(ctx.graph, target.key)
    if shape is not None and shape.kind is MethodKind.DYNAMIC:
        raise ConversionGap(
            f"Run VI: {target.name} is a dynamic dispatch member VI, which runs "
            "only with an object of its class.",
            node,
        )
    for terminal in ctx.graph.vi_interface(target.key)[0]:
        if not terminal.is_error_cluster and is_required(terminal, "input"):
            raise ConversionGap(
                f"Run VI passes no inputs; {terminal.name or terminal.index!r} of "
                f"{target.name} is Required.",
                node,
            )
    setter = _value_set_on(node, ctx, target)
    if setter is not None:
        raise ConversionGap(
            f"Run VI: the diagram sets a value on the VI reference ({setter}), which "
            "the run would use; a call with no arguments cannot pass it.",
            node,
        )


def _value_set_on(
    node: PrimitiveNode, ctx: CodeGenContext, target: ReferencedVI
) -> str | None:
    """The name of a node of this diagram that changes the VI ``target`` through
    a reference to it: an Invoke Node other than Run VI and the Ctrl Val methods
    (Set Control Value, ...) or a Property Node that writes a property. None when
    there is none."""
    assert ctx.graph is not None and ctx.vi_name is not None
    for other in ctx.graph.iter_nodes(ctx.vi_name):
        if (
            other is node
            or not isinstance(other, PrimitiveNode)
            or other.object_name != VI_CLASS
            or ctrl_val.is_ctrl_val(other)
        ):
            continue
        reference = _input(other, _REFERENCE_IN)
        if reference is None or not ctx.is_wired(reference.id):
            continue
        try:
            held = ctx.graph.referenced_vi(reference.id)
        except VIReferenceUnresolved:
            continue
        if held.key != target.key:
            continue
        if other.method_name and not is_run_vi(other):
            return f"Invoke Node {other.method_name}"
        if other.properties and _writes(other, ctx):
            return "Property Node " + ", ".join(p.name for p in other.properties)
    return None


def _writes(node: PrimitiveNode, ctx: CodeGenContext) -> bool:
    """Whether a Property Node writes a property: a value is wired into it
    besides its reference and error in."""
    return any(
        t.direction == "input"
        and t.index != _REFERENCE_IN
        and not t.is_error_cluster
        and ctx.is_wired(t.id)
        for t in node.terminals
    )

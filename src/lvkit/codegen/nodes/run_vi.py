"""Run VI, the method of a VI reference (VI class, method code 1003).

Run VI runs the VI with the current values of its front-panel controls, passing
no data through parameters (NI: properties-and-methods/vi-server/vi/run.html). A
VI is a Python function, so Run VI on a reference whose VI is known when the code
is generated (``InMemoryVIGraph.referenced_vi``) is a call of that function with
no arguments:

- Wait Until Done TRUE (its default, when unwired) is ``function()``;
- Wait Until Done FALSE starts the function and goes on, on the thread pool
  Start Asynchronous Call uses (``vi_reference.submit_to_pool``);
- Auto Dispose Ref only says who closes the reference: no Python meaning.

A gap where a call with no arguments cannot be that run: Wait Until Done computed
when the diagram runs; a VI with a Required input; a dynamic dispatch member VI;
and a diagram that sets a value on the same VI reference (an Invoke Node such as
Set Control Value, or a Property Node write), which the run would use."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.interface_order import is_required
from lvkit.graph.models import ConstantNode, PrimitiveNode
from lvkit.graph.vi_reference import VI_CLASS, ReferencedVI, VIReferenceUnresolved
from lvkit.models import Terminal

from ..ast_utils import parse_expr
from ..class_model import MethodKind, member_shape
from ..context import CodeGenContext, _format_constant
from ..fragment import CodeFragment
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
    """The call of the function of the VI the reference holds, waited for or
    started on the thread pool."""
    assert ctx.graph is not None
    reference = _input(node, _REFERENCE_IN)
    if reference is None or not ctx.is_wired(reference.id):
        raise ConversionGap("Run VI: the VI reference input is not wired.", node)
    try:
        target = ctx.graph.referenced_vi(reference.id)
    except VIReferenceUnresolved as problem:
        raise ConversionGap(f"Run VI: {problem}", node) from None
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
    a reference to it: an Invoke Node other than Run VI (Set Control Value,
    Ctrl Val.Set, ...) or a Property Node that writes a property. None when
    there is none."""
    assert ctx.graph is not None and ctx.vi_name is not None
    for other in ctx.graph.iter_nodes(ctx.vi_name):
        if (
            other is node
            or not isinstance(other, PrimitiveNode)
            or other.object_name != VI_CLASS
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

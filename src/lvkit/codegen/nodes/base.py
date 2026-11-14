"""Shared helpers for node code generators."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, PrimitiveNode
from lvkit.graph.vi_reference import is_this_application
from lvkit.models import Terminal

from ..context import CodeGenContext
from ..errors import CodeGenError

# Open Application Reference's primResID (``nodes/ops/open_application_reference``).
OPEN_APPLICATION_REFERENCE = 8012


class DefaultObjectError(ConversionGap):
    """A Property or Invoke Node acts on the default object of a VI Server class
    in a way that has no Python equivalent."""


def _ref_terminal(node: AnyGraphNode) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0),
        None,
    )


def _ref_value(
    ref_term: Terminal, node: AnyGraphNode, ctx: CodeGenContext
) -> str | None:
    """The Python name the reference input carries."""
    return ctx.wired_input(ref_term.id, node)


def _from_this_application(ref_term: Terminal, ctx: CodeGenContext) -> bool:
    """Whether the reference input is wired only from "This Application" constants:
    the running application, which is the default object of the Application class
    (the class a Property or Invoke Node acts on when its reference is unwired)."""
    if ctx.graph is None:
        return False
    origins = ctx.graph.wire_origins(ref_term.id)
    return bool(origins) and all(
        is_this_application(n) or _opens_local_application(n, ctx) for n, _ in origins
    )


def _opens_local_application(node: AnyGraphNode, ctx: CodeGenContext) -> bool:
    """Whether ``node`` is an Open Application Reference with no machine name
    wired: a reference to the local application, as "This Application" is."""
    if (
        not isinstance(node, PrimitiveNode)
        or node.prim_id != OPEN_APPLICATION_REFERENCE
    ):
        return False
    machine = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 11), None
    )
    return machine is None or not ctx.is_wired(machine.id)


def unwired_ref_class(node: AnyGraphNode, ctx: CodeGenContext) -> str | None:
    """The VI Server class whose default object a Property or Invoke Node acts
    on, when its reference input (terminal index 0) carries no value and is not
    wired, or is wired only from "This Application"; None when it has a value or is
    wired from anything else. NI documents that a Property or
    Invoke Node of the Application class defaults to the current application
    instance and one of the VI class to the VI containing the node
    (https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/property-node.html,
    .../functions/invoke-node.html)."""
    ref_term = _ref_terminal(node)
    if ref_term is not None and (
        _ref_value(ref_term, node, ctx) or ctx.is_wired(ref_term.id)
    ):
        if not _from_this_application(ref_term, ctx):
            return None
    class_name = getattr(node, "object_name", None)
    if not class_name:
        raise ConversionGap("Unwired object reference has no object class", node)
    return str(class_name)


def resolve_ref_input(node: AnyGraphNode, ctx: CodeGenContext) -> str:
    """The Python name a Property or Invoke Node reads its object from. A wired
    reference that has no value raises ``CodeGenError``. The caller handles an
    unwired reference first (see ``unwired_ref_class``)."""
    ref_term = _ref_terminal(node)
    value = _ref_value(ref_term, node, ctx) if ref_term is not None else None
    if value is None:
        raise CodeGenError("Wired object reference has no value", node)
    return value

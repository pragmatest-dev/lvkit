"""Shared helpers for node code generators."""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode
from lvkit.models import Terminal

from ..context import CodeGenContext
from ..errors import CodeGenError


class DefaultObjectError(ConversionGap):
    """A Property or Invoke Node acts on the default object of a VI Server class
    in a way that has no Python equivalent."""


def _ref_terminal(node: AnyGraphNode) -> Terminal | None:
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0),
        None,
    )


def _ref_value(ref_term: Terminal, ctx: CodeGenContext) -> str | None:
    """The Python name the reference input carries, directly or from its source."""
    resolved = ctx.resolve(ref_term.id)
    if resolved:
        return resolved
    flow = ctx.get_source(ref_term.id)
    if flow and flow.src_terminal:
        return ctx.resolve(flow.src_terminal) or None
    return None


def unwired_ref_class(node: AnyGraphNode, ctx: CodeGenContext) -> str | None:
    """The VI Server class whose default object a Property or Invoke Node acts
    on, when its reference input (terminal index 0) carries no value and is not
    wired; None when it has a value or is wired. NI documents that a Property or
    Invoke Node of the Application class defaults to the current application
    instance and one of the VI class to the VI containing the node
    (https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/property-node.html,
    .../functions/invoke-node.html)."""
    ref_term = _ref_terminal(node)
    if ref_term is not None and (
        _ref_value(ref_term, ctx) or ctx.is_wired(ref_term.id)
    ):
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
    value = _ref_value(ref_term, ctx) if ref_term is not None else None
    if value is None:
        raise CodeGenError("Wired object reference has no value", node)
    return value

"""Shared helpers for node code generators."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from lvkit.graph.models import AnyGraphNode

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..elementwise import LV_IMPORT
from ..errors import CodeGenError


@dataclass
class RefInput:
    """The Python name a Property or Invoke Node reads its object from, plus the
    statements and imports that define it."""

    name: str
    statements: list[ast.stmt] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)


def resolve_ref_input(node: AnyGraphNode, ctx: CodeGenContext) -> RefInput:
    """Resolve the object reference input (terminal index 0) of a Property or
    Invoke Node.

    A wired reference uses the wired value. An unwired reference uses the
    default object of the node's class: NI documents that a Property or Invoke
    Node of the Application class defaults to the current application
    instance and one of the VI class to the VI containing the node
    (https://docs-be.ni.com/api/bundle/labview-api-ref/page/functions/property-node.html,
    .../functions/invoke-node.html). The default is assigned to a variable by a
    leading statement. A wired reference that has no value raises
    ``CodeGenError``.
    """
    ref_term = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0),
        None,
    )
    if ref_term is not None:
        resolved = ctx.resolve(ref_term.id)
        if resolved:
            return RefInput(resolved)
        flow = ctx.get_source(ref_term.id)
        if flow and flow.src_terminal:
            resolved = ctx.resolve(flow.src_terminal)
            if resolved:
                return RefInput(resolved)
        if ctx.is_wired(ref_term.id):
            raise CodeGenError("Wired object reference has no value", node)

    class_name = getattr(node, "object_name", None)
    if not class_name:
        raise CodeGenError("Unwired object reference has no object class", node)
    owner = ctx.qualified_vi_name or ctx.vi_name or ""
    var_name = ctx.make_output_var(class_name, node.id)
    call = parse_expr(
        f"_lv.default_reference({class_name!r}, owner={owner!r})"
    )
    return RefInput(var_name, [build_assign(var_name, call)], {LV_IMPORT})

"""Node-specific code generators.

generate(node, ctx) is the single entry point. isinstance dispatch narrows
the GraphNode subtype, dispatches to the appropriate module function.
No classes — each module exposes generate(node, ctx) + helpers.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.graph.core import _graph_node_to_op_kind
from lvkit.graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    ConstantNode,
    EventStructureNode,
    FormulaNode,
    InPlaceNode,
    LoopNode,
    PrimitiveNode,
    SequenceNode,
    VINode,
)

from ..context import CodeGenContext
from ..fragment import CodeFragment

# Import modules (not classes) for dispatch
from . import (
    case,
    compound,
    constant,
    event,
    first_call,
    formula,
    in_place,
    invoke_node,
    loop,
    nmux,
    primitive,
    printf,
    property_node,
    queue_ops,
    sequence,
    subvi,
)
from .base import CodeGenError, MissingDependencyError, UnknownNodeError


def generate(node: AnyGraphNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a graph node.

    isinstance-based dispatch on the typed ``GraphNode`` subtype, calling the
    right module. A ``PrimitiveNode`` fans out to property/invoke/primitive
    by its own fields (``.properties``/``.method_name``/plain primitive).
    """
    # Structures first (StructureNode subtypes). DisableStructureNode is left
    # to the unknown fallback (no dedicated generator yet) — it is NOT a
    # CaseStructureNode subclass, so the case branch won't swallow it.
    if isinstance(node, InPlaceNode):
        return in_place.generate(node, ctx)
    if isinstance(node, CaseStructureNode):
        return case.generate(node, ctx)
    if isinstance(node, LoopNode):
        return loop.generate(node, ctx)
    if isinstance(node, SequenceNode):
        return sequence.generate(node, ctx)
    if isinstance(node, EventStructureNode):
        return event.generate(node, ctx)
    if isinstance(node, VINode):
        return subvi.generate(node, ctx)
    if isinstance(node, FormulaNode):
        return formula.generate(node, ctx)
    if isinstance(node, PrimitiveNode):
        # A Feedback Node is a PrimitiveNode carrying feedback attrs on the
        # graph. Faithful Python codegen isn't implemented yet -- fail loud
        # rather than emit a silently wrong body (describe/netlist DO model it).
        if ctx.feedback_is_master(node) is not None:
            raise UnknownNodeError(
                f"Feedback Node codegen not yet supported "
                f"(id={node.id}, name={node.name or 'Feedback Node'})"
            )
        if node.properties:
            return property_node.generate(node, ctx)
        if node.method_name:
            return invoke_node.generate(node, ctx)
        return _generate_primitive(node, ctx)
    if isinstance(node, ConstantNode):
        return constant.generate(node, ctx)
    return _generate_unknown(node)


# node_type -> codegen function, for a primitive node. The bodies already
# live in per-type modules (compound/nmux/printf); this registry replaces the
# inlined match so a new prim node type registers here instead of editing a
# switch. The default (primitive.generate) is the "GenericHandler" of this stage.
_PrimGen = Callable[[PrimitiveNode, CodeGenContext], CodeFragment]
_PRIM_CODEGEN: dict[str, _PrimGen] = {
    "cpdArith": compound.generate_compound_arith,
    "aBuild": compound.generate_array_build,
    "aInit": compound.generate_array_init,
    "aReplace": compound.generate_array_replace,
    "aInsert": compound.generate_array_insert,
    "aReshape": compound.generate_array_reshape,
    "nMux": nmux.generate,
    "mux": nmux.generate,
    "demux": nmux.generate,
    "printf": printf.generate,
}


def _generate_primitive(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Secondary dispatch for a PrimitiveNode by node_type."""
    # Queue Operations (Obtain/Enqueue/Dequeue/Get Queue Status) are
    # generic `class="prim"` XML nodes distinguished only by prim_id
    # (verified against JKI-VI-Tester samples) -- node_type alone can't
    # tell them apart, so dispatch by prim_id before the node_type registry.
    if node.node_type == "prim" and node.prim_id in queue_ops.QUEUE_PRIM_IDS:
        return queue_ops.generate(node, ctx)
    # First Call? (1083) is likewise a generic `class="prim"` node
    # distinguished only by prim_id -- see nodes/first_call.py.
    if node.node_type == "prim" and node.prim_id == first_call.FIRST_CALL_PRIM_ID:
        return first_call.generate(node, ctx)
    handler = _PRIM_CODEGEN.get(node.node_type or "", primitive.generate)
    return handler(node, ctx)


def _generate_unknown(node: AnyGraphNode) -> CodeFragment:
    """Emit a warning comment for unsupported node types."""
    node_name = node.name or "unknown"
    kind = _graph_node_to_op_kind(node)
    warning = f"# WARNING: Unknown node type {kind} (id={node.id}, name={node_name})"
    stmt = ast.Expr(value=ast.Constant(value=warning))
    return CodeFragment(statements=[stmt])


__all__ = [
    "CodeGenError",
    "MissingDependencyError",
    "UnknownNodeError",
    "generate",
]

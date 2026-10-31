"""Node-specific code generators.

generate(node, ctx) is the single entry point. isinstance dispatch narrows
the GraphNode subtype, dispatches to the appropriate module function.
No classes — each module exposes generate(node, ctx) + helpers.
"""

from __future__ import annotations

from collections.abc import Callable

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.core import _graph_node_to_op_kind
from lvkit.graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    ConstantNode,
    DisableStructureNode,
    EventStructureNode,
    FormulaNode,
    GlobalVariableNode,
    InPlaceNode,
    LocalVariableNode,
    LoopNode,
    PrimitiveNode,
    SequenceNode,
    SharedVariableNode,
    VINode,
)

from ..context import CodeGenContext
from ..errors import CodeGenError, UnknownNodeError
from ..fragment import CodeFragment
from ..gaps import catch_gap

# Import modules (not classes) for dispatch
from . import (
    case,
    compound,
    constant,
    disable,
    event,
    ext_func,
    first_call,
    formula,
    global_variable,
    in_place,
    invoke_node,
    local_variable,
    loop,
    nmux,
    primitive,
    printf,
    property_node,
    queue_ops,
    sequence,
    shared_variable,
    subvi,
    vi_reference,
)


def generate(node: AnyGraphNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a graph node. A node that cannot be converted (a
    ``ConversionGap``) is recorded as a gap and generates the ``raise`` that
    stands where it would run (see ``gaps.py``)."""
    with catch_gap(node, ctx) as guard:
        return _generate(node, ctx)
    assert guard.fragment is not None
    return guard.fragment


def _generate(node: AnyGraphNode, ctx: CodeGenContext) -> CodeFragment:
    """Dispatch a graph node to the module that generates it.

    isinstance-based dispatch on the typed ``GraphNode`` subtype, calling the
    right module. A ``PrimitiveNode`` fans out to property/invoke/primitive
    by its own fields (``.properties``/``.method_name``/plain primitive).
    """
    circular = ctx.circular_nodes.get(node.id)
    if circular is not None:
        raise ConversionGap(circular, node)
    # Structures first (StructureNode subtypes). DisableStructureNode is NOT a
    # CaseStructureNode subclass: it has no runtime selector.
    if isinstance(node, InPlaceNode):
        return in_place.generate(node, ctx)
    if isinstance(node, CaseStructureNode):
        return case.generate(node, ctx)
    if isinstance(node, LoopNode):
        return loop.generate(node, ctx)
    if isinstance(node, DisableStructureNode):
        return disable.generate(node, ctx)
    if isinstance(node, SequenceNode):
        return sequence.generate(node, ctx)
    if isinstance(node, EventStructureNode):
        return event.generate(node, ctx)
    if isinstance(node, VINode):
        if node.node_type == "callByRefNode":
            return vi_reference.generate_call(node, ctx)
        return subvi.generate(node, ctx)
    if isinstance(node, FormulaNode):
        return formula.generate(node, ctx)
    if isinstance(node, GlobalVariableNode):
        return global_variable.generate(node, ctx)
    if isinstance(node, SharedVariableNode):
        return shared_variable.generate(node, ctx)
    if isinstance(node, PrimitiveNode):
        # A Feedback Node is a PrimitiveNode carrying feedback attrs on the
        # graph. Faithful Python codegen is not implemented: it is a gap
        # (describe/netlist do model it).
        if ctx.is_feedback_node(node):
            raise UnknownNodeError("Feedback Node codegen not yet supported", node)
        if node.properties:
            return property_node.generate(node, ctx)
        if node.method_name:
            return invoke_node.generate(node, ctx)
        return _generate_primitive(node, ctx)
    if isinstance(node, ConstantNode):
        return constant.generate(node, ctx)
    if isinstance(node, LocalVariableNode):
        return local_variable.generate(node, ctx)
    return _generate_unknown(node)


# node_type -> codegen function, for a primitive node. The bodies already
# live in per-type modules (compound/nmux/printf); this registry replaces the
# inlined match so a new prim node type registers here instead of editing a
# switch. The default (primitive.generate) is the "GenericHandler" of this stage.
_PrimGen = Callable[[PrimitiveNode, CodeGenContext], CodeFragment]
_PRIM_CODEGEN: dict[str, _PrimGen] = {
    "cpdArith": compound.generate_compound_arith,
    "aBuild": compound.generate_array_build,
    "concat": compound.generate_concat_strings,
    "aInit": compound.generate_array_init,
    "aReplace": compound.generate_array_replace,
    "aInsert": compound.generate_array_insert,
    "aReshape": compound.generate_array_reshape,
    "nMux": nmux.generate,
    "mux": nmux.generate,
    "demux": nmux.generate,
    "printf": printf.generate,
    "extFunc": ext_func.generate,
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
    # Open VI Reference and Close Reference are generic `class="prim"` nodes
    # too -- see nodes/vi_reference.py.
    if node.node_type == "prim":
        if node.prim_id == vi_reference.OPEN_VI_REFERENCE_PRIM_ID:
            return vi_reference.generate_open(node, ctx)
        if (
            node.prim_id == vi_reference.CLOSE_REFERENCE_PRIM_ID
            and vi_reference.closes_vi_reference(node, ctx)
        ):
            return CodeFragment.empty()
    handler = _PRIM_CODEGEN.get(node.node_type or "", primitive.generate)
    return handler(node, ctx)


def _generate_unknown(node: AnyGraphNode) -> CodeFragment:
    """A node type with no generator is a gap."""
    kind = _graph_node_to_op_kind(node)
    raise UnknownNodeError(f"Unknown node type {kind}", node)


__all__ = [
    "CodeGenError",
    "UnknownNodeError",
    "generate",
]

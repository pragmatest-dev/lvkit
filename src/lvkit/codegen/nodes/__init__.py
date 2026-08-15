"""Node-specific code generators.

generate(node, ctx) is the single entry point. Match narrows the
Operation subtype, dispatches to the appropriate module function.
No classes — each module exposes generate(node, ctx) + helpers.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.models import (
    CaseOperation,
    EventOperation,
    FeedbackOperation,
    FormulaOperation,
    InPlaceOperation,
    InvokeOperation,
    LoopOperation,
    Operation,
    PrimitiveOperation,
    PropertyOperation,
    SequenceOperation,
    SubVIOperation,
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


def generate(node: Operation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an operation node.

    Match-based dispatch: narrows the type, calls the right module.
    """
    match node:
        case InPlaceOperation():
            return in_place.generate(node, ctx)
        case CaseOperation():
            return case.generate(node, ctx)
        case LoopOperation():
            return loop.generate(node, ctx)
        case SequenceOperation():
            return sequence.generate(node, ctx)
        case EventOperation():
            return event.generate(node, ctx)
        case PropertyOperation():
            return property_node.generate(node, ctx)
        case InvokeOperation():
            return invoke_node.generate(node, ctx)
        case SubVIOperation():
            return subvi.generate(node, ctx)
        case FormulaOperation():
            return formula.generate(node, ctx)
        case FeedbackOperation():
            # A Feedback Node carries loop-iteration state (a z^-N mu). Faithful
            # Python codegen isn't implemented yet -- fail loud rather than emit
            # a silently wrong body (the describe/netlist layer DOES model it).
            raise UnknownNodeError(
                f"Feedback Node codegen not yet supported "
                f"(id={node.id}, name={node.name or 'Feedback Node'})"
            )
        case PrimitiveOperation():
            return _generate_primitive(node, ctx)
        case _ if node.kind == "constant":
            return constant.generate(node, ctx)
        case _:
            return _generate_unknown(node)


# node_type -> codegen function, for a PrimitiveOperation. The bodies already
# live in per-type modules (compound/nmux/printf); this registry replaces the
# inlined match so a new prim node type registers here instead of editing a
# switch. The default (primitive.generate) is the "GenericHandler" of this stage.
_PrimGen = Callable[[PrimitiveOperation, CodeGenContext], CodeFragment]
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
    node: PrimitiveOperation,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Secondary dispatch for PrimitiveOperation by node_type."""
    # Queue Operations (Obtain/Enqueue/Dequeue/Get Queue Status) are
    # generic `class="prim"` XML nodes distinguished only by primResID
    # (verified against JKI-VI-Tester samples) -- node_type alone can't
    # tell them apart, so dispatch by primResID before the node_type registry.
    if node.node_type == "prim" and node.primResID in queue_ops.QUEUE_PRIM_IDS:
        return queue_ops.generate(node, ctx)
    # First Call? (1083) is likewise a generic `class="prim"` node
    # distinguished only by primResID -- see nodes/first_call.py.
    if node.node_type == "prim" and node.primResID == first_call.FIRST_CALL_PRIM_ID:
        return first_call.generate(node, ctx)
    handler = _PRIM_CODEGEN.get(node.node_type or "", primitive.generate)
    return handler(node, ctx)


def _generate_unknown(node: Operation) -> CodeFragment:
    """Emit a warning comment for unsupported node types."""
    node_name = node.name or "unknown"
    warning = (
        f"# WARNING: Unknown node type {node.kind} (id={node.id}, name={node_name})"
    )
    stmt = ast.Expr(value=ast.Constant(value=warning))
    return CodeFragment(statements=[stmt])


__all__ = [
    "CodeGenError",
    "MissingDependencyError",
    "UnknownNodeError",
    "generate",
]

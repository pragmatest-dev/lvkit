"""Node-specific code generators.

generate(node, ctx) is the single entry point. Match narrows the
Operation subtype, dispatches to the appropriate module function.
No classes — each module exposes generate(node, ctx) + helpers.
"""

from __future__ import annotations

import ast

from lvpy.graph_types import (
    CaseOperation,
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
    invoke_node,
    loop,
    nmux,
    primitive,
    printf,
    property_node,
    sequence,
    subvi,
)
from .base import CodeGenError, MissingDependencyError, UnknownNodeError


def generate(node: Operation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an operation node.

    Match-based dispatch: narrows the type, calls the right module.
    """
    match node:
        case CaseOperation():
            return case.generate(node, ctx)
        case LoopOperation():
            return loop.generate(node, ctx)
        case SequenceOperation():
            return sequence.generate(node, ctx)
        case PropertyOperation():
            return property_node.generate(node, ctx)
        case InvokeOperation():
            return invoke_node.generate(node, ctx)
        case SubVIOperation():
            return subvi.generate(node, ctx)
        case PrimitiveOperation():
            return _generate_primitive(node, ctx)
        case _ if "Constant" in node.labels:
            return constant.generate(node, ctx)
        case _:
            return _generate_unknown(node)


def _generate_primitive(
    node: PrimitiveOperation, ctx: CodeGenContext,
) -> CodeFragment:
    """Secondary dispatch for PrimitiveOperation by node_type."""
    match node.node_type:
        case "cpdArith":
            return compound.generate_compound_arith(node, ctx)
        case "aBuild":
            return compound.generate_array_build(node, ctx)
        case "nMux":
            return nmux.generate(node, ctx)
        case "printf":
            return printf.generate(node, ctx)
        case _:
            return primitive.generate(node, ctx)


def _generate_unknown(node: Operation) -> CodeFragment:
    """Emit a warning comment for unsupported node types."""
    node_name = node.name or "unknown"
    warning = (
        f"# WARNING: Unknown node type {node.labels}"
        f" (id={node.id}, name={node_name})"
    )
    stmt = ast.Expr(value=ast.Constant(value=warning))
    return CodeFragment(statements=[stmt])


__all__ = [
    "CodeGenError",
    "MissingDependencyError",
    "UnknownNodeError",
    "generate",
]

"""Error classes and shared helpers for node code generators."""

from __future__ import annotations

from lvpy.models import Operation

from ..ast_utils import to_var_name
from ..context import CodeGenContext


class CodeGenError(Exception):
    """Raised when code generation fails for a node."""

    def __init__(self, message: str, node: Operation | None = None):
        self.node = node
        self.node_id = node.id if node else None
        self.node_name = node.name if node else None
        super().__init__(message)


class UnknownNodeError(CodeGenError):
    """Raised when encountering an unsupported node type."""

    pass


class MissingDependencyError(CodeGenError):
    """Raised when a required dependency (SubVI, primitive) is missing."""

    pass


def resolve_ref_input(node: Operation, ctx: CodeGenContext) -> str:
    """Resolve the object reference input (typically terminal index 0).

    Used by property_node and invoke_node modules.
    """
    for term in node.terminals:
        if term.direction == "input" and term.index == 0:
            resolved = ctx.resolve(term.id)
            if resolved:
                return resolved
            flow = ctx.get_source(term.id)
            if flow and flow.src_terminal:
                resolved = ctx.resolve(flow.src_terminal)
                if resolved:
                    return resolved

    obj_name = getattr(node, "object_name", None) or "ref"
    return to_var_name(obj_name)

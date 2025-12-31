"""Base class for node code generators."""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import CodeGenContext
    from ..fragment import CodeFragment


class CodeGenError(Exception):
    """Raised when code generation fails for a node."""

    def __init__(self, message: str, node: dict[str, Any] | None = None):
        self.node = node
        self.node_id = node.get("id") if node else None
        self.node_name = node.get("name") if node else None
        super().__init__(message)


class UnknownNodeError(CodeGenError):
    """Raised when encountering an unsupported node type."""

    pass


class MissingDependencyError(CodeGenError):
    """Raised when a required dependency (SubVI, primitive) is missing."""

    pass


class NodeCodeGen(ABC):
    """Abstract base class for node-specific code generation.

    Each node type (Primitive, SubVI, Loop, etc.) has its own CodeGen
    that knows how to generate AST fragments for that node.
    """

    @abstractmethod
    def generate(self, node: dict[str, Any], ctx: CodeGenContext) -> CodeFragment:
        """Generate code fragment for this node.

        Args:
            node: Node data from vi_context operations
            ctx: Code generation context with bindings

        Returns:
            CodeFragment with AST statements and new bindings

        Raises:
            CodeGenError: If code generation fails
        """
        pass


def get_codegen(node: dict[str, Any], strict: bool = False) -> NodeCodeGen:
    """Factory: return appropriate CodeGen for a node.

    Args:
        node: Node data with 'labels' indicating type
        strict: If True, raise UnknownNodeError for unsupported nodes

    Returns:
        Appropriate NodeCodeGen instance

    Raises:
        UnknownNodeError: If strict=True and node type is not recognized
    """
    # Import here to avoid circular imports
    from .constant import ConstantCodeGen
    from .loop import LoopCodeGen
    from .primitive import PrimitiveCodeGen
    from .subvi import SubVICodeGen

    labels = node.get("labels", [])

    # Check for loop structures
    if node.get("loop_type") in ("whileLoop", "forLoop"):
        return LoopCodeGen()

    # Check for SubVI
    if "SubVI" in labels:
        return SubVICodeGen()

    # Check for Primitive
    if "Primitive" in labels:
        return PrimitiveCodeGen()

    # Check for Constant
    if "Constant" in labels:
        return ConstantCodeGen()

    # Unknown node type
    if strict:
        node_id = node.get("id", "?")
        node_name = node.get("name", "unknown")
        raise UnknownNodeError(
            f"Unknown node type: {labels} (id={node_id}, name={node_name})",
            node=node,
        )

    # Default: placeholder generator that emits a warning comment
    return UnknownNodeCodeGen()


class UnknownNodeCodeGen(NodeCodeGen):
    """Generator for unsupported node types - emits warning comment."""

    def generate(self, node: dict[str, Any], ctx: CodeGenContext) -> CodeFragment:
        from ..fragment import CodeFragment

        node_id = node.get("id", "?")
        node_name = node.get("name", "unknown")
        labels = node.get("labels", [])

        # Emit a comment as a string expression so it's visible in output
        warning = f"# WARNING: Unknown node type {labels} (id={node_id}, name={node_name})"
        stmt = ast.Expr(value=ast.Constant(value=warning))

        return CodeFragment(statements=[stmt])

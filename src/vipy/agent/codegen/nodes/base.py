"""Base class for node code generators."""

from __future__ import annotations

import ast
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..fragment import CodeFragment

if TYPE_CHECKING:
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


class NodeCodeGen(ABC):
    """Abstract base class for node-specific code generation.

    Each node type (Primitive, SubVI, Loop, etc.) has its own CodeGen
    that knows how to generate AST fragments for that node.
    """

    @abstractmethod
    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code fragment for this node.

        Args:
            node: Operation dataclass from vi_context
            ctx: Code generation context with bindings

        Returns:
            CodeFragment with AST statements and new bindings

        Raises:
            CodeGenError: If code generation fails
        """
        pass


class UnknownNodeCodeGen(NodeCodeGen):
    """Generator for unsupported node types - emits warning comment."""

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        node_id = node.id
        node_name = node.name or "unknown"
        labels = node.labels

        # Emit a comment as a string expression so it's visible in output
        warning = (
            f"# WARNING: Unknown node type {labels}"
            f" (id={node_id}, name={node_name})"
        )
        stmt = ast.Expr(value=ast.Constant(value=warning))

        return CodeFragment(statements=[stmt])

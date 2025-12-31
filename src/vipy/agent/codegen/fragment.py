"""Code fragment - output of a node's code generation."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class CodeFragment:
    """Output of a single node's code generation.

    Contains:
    - statements: AST nodes to emit in the function body
    - bindings: New terminal → variable mappings this node creates
    - imports: Import statements this code requires
    """

    statements: list[ast.stmt] = field(default_factory=list)
    bindings: dict[str, str] = field(default_factory=dict)
    imports: set[str] = field(default_factory=set)

    @classmethod
    def empty(cls) -> CodeFragment:
        """Create an empty fragment (no-op)."""
        return cls()

    @classmethod
    def from_statement(
        cls,
        stmt: ast.stmt,
        bindings: dict[str, str] | None = None,
        imports: set[str] | None = None,
    ) -> CodeFragment:
        """Create a fragment from a single statement."""
        return cls(
            statements=[stmt],
            bindings=bindings or {},
            imports=imports or set(),
        )

    def extend(self, other: CodeFragment) -> None:
        """Extend this fragment with another's contents."""
        self.statements.extend(other.statements)
        self.bindings.update(other.bindings)
        self.imports.update(other.imports)

    def __add__(self, other: CodeFragment) -> CodeFragment:
        """Combine two fragments."""
        return CodeFragment(
            statements=self.statements + other.statements,
            bindings={**self.bindings, **other.bindings},
            imports=self.imports | other.imports,
        )

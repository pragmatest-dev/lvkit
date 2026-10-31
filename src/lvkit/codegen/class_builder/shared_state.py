"""What the methods of one class module share: the module-level code their
bodies need (helper functions, ``ctypes`` declarations, globals), the files they
read, and the imports they collect."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from ..context import CodeGenContext
from ..module_declarations import ModuleDeclarations


@dataclass
class ModuleParts:
    """The module-level parts every method of a class module adds to."""

    module_stem: str
    data_files: dict[str, bytes] | None = None
    imports: set[str] = field(default_factory=set)
    annotation_imports: set[str] = field(default_factory=set)
    declarations: ModuleDeclarations = field(default_factory=ModuleDeclarations)
    helpers: list[ast.stmt] = field(default_factory=list)
    globals: dict[str, ast.stmt] = field(default_factory=dict)
    flat_functions: dict[tuple[Any, ...], str] = field(default_factory=dict)

    def share_with(self, ctx: CodeGenContext) -> None:
        """Make ``ctx`` add its module-level code to this module's, so two
        methods never declare one name twice."""
        ctx.module_declarations = self.declarations
        ctx.formula_helpers = self.helpers
        ctx.module_globals = self.globals
        ctx.flat_functions = self.flat_functions

    def collect(self, ctx: CodeGenContext) -> None:
        """Take the imports ``ctx`` needs for annotations."""
        self.annotation_imports.update(s for s in ctx.annotation_imports if s)

    def statements(self) -> list[ast.stmt]:
        """The module-level code the methods declared, in definition order."""
        return [
            *self.helpers,
            *self.declarations.statements,
            *(self.globals[name] for name in sorted(self.globals)),
        ]

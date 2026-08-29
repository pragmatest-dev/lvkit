"""Build Python class AST from LabVIEW class (lvclass)."""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.structure import LVClass, LVMethod

from ..ast_optimizer import optimize_module
from ..ast_utils import to_var_name
from ._shared import _EMPTY_VI_CONTEXT
from .config import ClassConfig
from .init_builder import _InitBuilderMixin
from .methods import _MethodBuilderMixin
from .naming import _NamingMixin
from .properties import _PropertyBuilderMixin
from .terminals import _TerminalClassifierMixin


class ClassBuilder(
    _InitBuilderMixin,
    _PropertyBuilderMixin,
    _MethodBuilderMixin,
    _TerminalClassifierMixin,
    _NamingMixin,
):
    """Build Python class AST from LabVIEW class.

    Handles:
    - Class definition with inheritance
    - __init__ from private data
    - Instance methods (with self)
    - Static methods (@staticmethod)
    - Properties from getter/setter pairs
    - Visibility mapping (public, protected, private)
    """

    def __init__(
        self,
        config: ClassConfig | None = None,
    ) -> None:
        self.config = config or ClassConfig()
        self._method_contexts: dict[str, VIContext] = {}
        self._import_resolver: Callable[[str], str] | None = None

    def build_class_module(
        self,
        lvclass: LVClass,
        method_contexts: dict[str, VIContext] | None = None,
        parent_class_name: str | None = None,
        import_resolver: Callable[[str], str] | None = None,
        graph: InMemoryVIGraph | None = None,
    ) -> ast.Module:
        """Build complete module with class definition.

        Args:
            lvclass: Parsed LVClass object
            method_contexts: Dict mapping method name to VI context
            parent_class_name: Parent class name (overrides lvclass.parent_class)
            import_resolver: Callable to resolve import paths for SubVIs

        Returns:
            AST Module with imports and class definition
        """
        self._method_contexts = method_contexts or {}
        self._import_resolver = import_resolver
        self._graph = graph
        self._collected_imports: set[str] = set()
        parent = parent_class_name or lvclass.parent_class

        module_body: list[ast.stmt] = []

        # Build class definition (collects imports from method bodies)
        class_def = self._build_class_def(lvclass, parent)

        # Build imports (static + collected from methods)
        module_body.extend(self._build_imports(lvclass, parent))
        module_body.append(class_def)

        module = ast.Module(body=module_body, type_ignores=[])

        # Run optimizer (dead code, unreachable, duplicate imports)
        module = optimize_module(module)

        return module

    def _build_imports(
        self,
        lvclass: LVClass,
        parent_class_name: str | None,
    ) -> list[ast.stmt]:
        """Build import statements for the module."""
        imports: list[ast.stmt] = []

        # Future annotations
        imports.append(
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations", asname=None)],
                level=0,
            )
        )

        # Common imports
        common = [
            "from pathlib import Path",
            "from typing import Any, ClassVar",
        ]
        for imp in common:
            try:
                tree = ast.parse(imp)
                imports.extend(tree.body)
            except SyntaxError:
                pass

        # Parent class import
        if parent_class_name:
            parent_module = to_var_name(parent_class_name.replace(".lvclass", ""))
            parent_class = self._to_class_name(parent_class_name)
            imports.append(
                ast.ImportFrom(
                    module=f".{parent_module}",
                    names=[ast.alias(name=parent_class, asname=None)],
                    level=0,
                )
            )

        # Add imports collected from method body generation
        for imp_str in sorted(self._collected_imports):
            try:
                tree = ast.parse(imp_str)
                imports.extend(tree.body)
            except SyntaxError:
                pass

        return imports

    def _build_class_def(
        self,
        lvclass: LVClass,
        parent_class_name: str | None,
    ) -> ast.ClassDef:
        """Build the class definition AST node."""
        class_name = self._to_class_name(lvclass.name)

        # Base classes
        bases: list[ast.expr] = []
        if parent_class_name:
            parent_class = self._to_class_name(parent_class_name)
            bases.append(ast.Name(id=parent_class, ctx=ast.Load()))

        # Class body
        body: list[ast.stmt] = []

        # Docstring
        if self.config.include_docstrings:
            body.append(
                ast.Expr(
                    value=ast.Constant(
                        value=f"Converted from LabVIEW class: {lvclass.name}."
                    )
                )
            )

        # __init__ method
        init_method = self._build_init(lvclass, parent_class_name)
        body.append(init_method)

        # Group methods by type for ordering
        accessors: list[LVMethod] = []
        public_methods: list[LVMethod] = []
        protected_methods: list[LVMethod] = []
        private_methods: list[LVMethod] = []

        for method in lvclass.methods:
            if method.is_accessor:
                accessors.append(method)
            elif method.scope == "public":
                public_methods.append(method)
            elif method.scope == "protected":
                protected_methods.append(method)
            else:  # private
                private_methods.append(method)

        # Build properties from accessor pairs
        property_defs = self._build_properties(accessors)
        body.extend(property_defs)

        # Separate truly static methods from instance methods
        # A method is an instance method if it has a class-typed input wire
        actual_static: list[LVMethod] = []
        actual_instance: list[LVMethod] = []
        for method in public_methods + protected_methods + private_methods:
            vi_ctx = self._method_contexts.get(method.name, _EMPTY_VI_CONTEXT)
            has_class_wire = any(
                self._is_self_input(inp, lvclass.name) for inp in vi_ctx.inputs
            )
            if has_class_wire:
                actual_instance.append(method)
            else:
                actual_static.append(method)

        # Build static methods (no class wire input)
        for method in actual_static:
            method_def = self._build_static_method(method, lvclass.name)
            body.append(method_def)

        # Build instance methods (have class wire input)
        for method in actual_instance:
            # Skip constructor-like methods (handled in __init__)
            if self._is_constructor(method.name):
                continue
            # Add scope prefix for non-public methods
            prefix = ""
            if method.scope == "protected":
                prefix = self.config.protected_prefix
            elif method.scope == "private":
                prefix = self.config.private_prefix
            try:
                method_def = self._build_instance_method(
                    method,
                    lvclass.name,
                    prefix=prefix,
                )
                body.append(method_def)
            except Exception:
                # Method codegen failed (e.g. unresolved terminals) — skip
                pass

        # Ensure body is not empty
        if not body:
            body.append(ast.Pass())

        return ast.ClassDef(
            name=class_name,
            bases=bases,
            keywords=[],
            body=body,
            decorator_list=[],
        )


def build_class(
    lvclass: LVClass,
    method_contexts: dict[str, VIContext] | None = None,
    parent_class_name: str | None = None,
    config: ClassConfig | None = None,
) -> str:
    """Build Python code from a LabVIEW class.

    Convenience function that creates a ClassBuilder and builds the module.

    Args:
        lvclass: Parsed LVClass object
        method_contexts: Dict mapping method name to VI context
        parent_class_name: Parent class name (overrides lvclass.parent_class)
        config: Optional ClassConfig

    Returns:
        Python source code as string
    """
    builder = ClassBuilder(config=config)
    module = builder.build_class_module(
        lvclass,
        method_contexts=method_contexts,
        parent_class_name=parent_class_name,
    )
    ast.fix_missing_locations(module)
    return ast.unparse(module)

"""Build Python class AST from LabVIEW class (lvclass)."""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.structure import LVClass, LVMethod

from ..ast_optimizer import optimize_module
from ..ast_utils import build_type_checking_block
from ..class_attributes import (
    Accessor,
    AccessorKind,
    ClassAttributes,
    class_attributes,
    scope_prefix,
)
from ..gaps import Gap, raise_statement
from ..import_resolver import ImportResolver, NameImport
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
        self._import_resolver: ImportResolver | None = None
        self._graph: InMemoryVIGraph | None = None
        self._attributes: ClassAttributes | None = None

    def build_class_module(
        self,
        lvclass: LVClass,
        method_contexts: dict[str, VIContext] | None = None,
        import_resolver: ImportResolver | None = None,
        graph: InMemoryVIGraph | None = None,
        gaps: list[Gap] | None = None,
    ) -> ast.Module:
        """Build complete module with class definition.

        Args:
            lvclass: Parsed LVClass object
            method_contexts: Dict mapping method name to VI context
            import_resolver: Callable to resolve import paths for SubVIs
            gaps: Receives the gaps found in the methods

        Returns:
            AST Module with imports and class definition
        """
        self._method_contexts = method_contexts or {}
        self._import_resolver = import_resolver
        self._graph = graph
        self._attributes = self._attributes_of(lvclass)
        self._gaps: list[Gap] = gaps if gaps is not None else []
        self._collected_imports: set[str] = set()
        self._collected_annotation_imports: set[str] = set()
        module_body: list[ast.stmt] = []
        try:
            parent = self._parent_import(lvclass)
        except ConversionGap as gap:
            # The class's type module reports this gap; this module raises it too.
            return ast.Module(
                body=[
                    ast.ImportFrom(
                        module="__future__",
                        names=[ast.alias(name="annotations")],
                        level=0,
                    ),
                    raise_statement(str(gap)),
                ],
                type_ignores=[],
            )

        # Build class definition (collects imports from method bodies)
        class_def = self._build_class_def(lvclass, parent)

        # Build imports (static + collected from methods)
        module_body.extend(self._build_imports(parent))
        module_body.append(class_def)

        module = ast.Module(body=module_body, type_ignores=[])

        # Run optimizer (dead code, unreachable, duplicate imports)
        module = optimize_module(module)

        return module

    def _parent_import(self, lvclass: LVClass) -> NameImport | None:
        """The import of the class this class descends from: the class the graph
        holds as its parent, imported from the module generated for it. A parent
        the class names that is not loaded is a gap."""
        key = self._class_key(lvclass)
        if key is None or self._graph is None or self._import_resolver is None:
            return None
        parent_key = self._graph.class_parent_key(key)
        if parent_key is not None:
            return self._import_resolver.node_import(parent_key)
        parent_name = self._graph.class_parent_name(key)
        if parent_name is not None:
            raise ConversionGap(f"Parent class {parent_name} of {key} is not loaded")
        return None

    def _class_key(self, lvclass: LVClass) -> str | None:
        """The graph key of the class: the path of its file."""
        key = str(lvclass.path.resolve())
        if self._graph is not None and self._graph.is_class(key):
            return key
        return None

    def _attributes_of(self, lvclass: LVClass) -> ClassAttributes | None:
        """The attributes and accessors of the class, None when the graph does
        not hold it."""
        key = self._class_key(lvclass)
        if key is None or self._graph is None:
            return None
        return class_attributes(self._graph, key)

    def _accessor_of(self, lvclass: LVClass, method: LVMethod) -> Accessor | None:
        """The accessor the class's property folders make of ``method``."""
        key = self._class_key(lvclass)
        if key is None or self._graph is None or self._attributes is None:
            return None
        found = self._graph.class_method(key, Path(method.vi_path).name)
        if found is None:
            return None
        return self._attributes.accessors.get(found[1])

    def _build_imports(self, parent: NameImport | None) -> list[ast.stmt]:
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
            "from typing import Any, ClassVar, NoReturn",
        ]
        for imp in common:
            try:
                tree = ast.parse(imp)
                imports.extend(tree.body)
            except SyntaxError:
                pass

        if parent is not None:
            imports.extend(ast.parse(parent.statement).body)

        # Add imports collected from method body generation
        for imp_str in sorted(self._collected_imports):
            try:
                tree = ast.parse(imp_str)
                imports.extend(tree.body)
            except SyntaxError:
                pass

        imports.extend(build_type_checking_block(self._collected_annotation_imports))

        return imports

    def _build_class_def(
        self,
        lvclass: LVClass,
        parent: NameImport | None,
    ) -> ast.ClassDef:
        """Build the class definition AST node."""
        class_name = self._to_class_name(lvclass.name)

        # Base classes
        bases: list[ast.expr] = []
        if parent is not None:
            bases.append(ast.Name(id=parent.name, ctx=ast.Load()))

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
        own_fields = self._attributes.own_fields if self._attributes else None
        if own_fields is not None and len(own_fields) != len(
            lvclass.private_data_fields
        ):
            own_fields = None
        init_method = self._build_init(lvclass, parent is not None, own_fields)
        body.append(init_method)

        # Property folders: an accessor that only moves its field is that
        # attribute and writes nothing; one with logic is a Python property;
        # any other VI, an accessor whose pane is neither included, is a method.
        property_members: list[tuple[LVMethod, Accessor]] = []

        # Group methods by scope for ordering
        public_methods: list[LVMethod] = []
        protected_methods: list[LVMethod] = []
        private_methods: list[LVMethod] = []

        for method in lvclass.methods:
            accessor = self._accessor_of(lvclass, method)
            if accessor is not None and accessor.kind is AccessorKind.ATTRIBUTE:
                continue
            if accessor is not None and accessor.kind is AccessorKind.PROPERTY:
                property_members.append((method, accessor))
            elif method.scope == "public":
                public_methods.append(method)
            elif method.scope == "protected":
                protected_methods.append(method)
            else:  # private
                private_methods.append(method)

        body.extend(self._build_properties(lvclass.name, property_members))

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
            prefix = scope_prefix(method.scope)
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
    config: ClassConfig | None = None,
) -> str:
    """Build Python code from a LabVIEW class.

    Convenience function that creates a ClassBuilder and builds the module.

    Args:
        lvclass: Parsed LVClass object
        method_contexts: Dict mapping method name to VI context
        config: Optional ClassConfig

    Returns:
        Python source code as string
    """
    builder = ClassBuilder(config=config)
    module = builder.build_class_module(
        lvclass,
        method_contexts=method_contexts,
    )
    ast.fix_missing_locations(module)
    return ast.unparse(module)

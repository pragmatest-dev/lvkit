"""Build the Python class of a LabVIEW class.

One LabVIEW class is one Python class in one module: it descends from the class
generated for its parent, sets the private data fields it declares in
``__init__``, and has the VIs it lists as methods and properties
(``class_model``). A parent that was not found leaves the class with no base, and
everything that would run raises (a gap)."""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph

from ..ast_optimizer import optimize_module
from ..ast_utils import build_type_checking_block, to_module_name
from ..class_attributes import Accessor, AccessorKind
from ..class_model import (
    ClassModel,
    MethodShape,
    base_import,
    class_model,
    declares_result_class,
    lazy_imports,
    result_class_imports,
    shares_refnums,
)
from ..context import type_namer
from ..errors import CodeGenError
from ..gaps import Gap, gap_of
from ..import_resolver import ImportResolver, NameImport
from ..type_fields import annotation_imports
from ..value_copy import COPY_IMPORT, holds_refnum, share_refnums_method
from .config import ClassConfig
from .init_builder import _InitBuilderMixin
from .methods import _MethodBuilderMixin
from .properties import _PropertyBuilderMixin
from .shared_state import ModuleParts

_COMMON_IMPORTS: tuple[str, ...] = (
    "from pathlib import Path",
    "from typing import Any, NamedTuple, NoReturn",
)


class ClassBuilder(_InitBuilderMixin, _PropertyBuilderMixin, _MethodBuilderMixin):
    """Build the Python class of a LabVIEW class."""

    def __init__(self, config: ClassConfig | None = None) -> None:
        self.config = config or ClassConfig()

    def build_class_module(
        self,
        graph: InMemoryVIGraph,
        class_key: str,
        import_resolver: ImportResolver,
        gaps: list[Gap] | None = None,
        data_files: dict[str, bytes] | None = None,
    ) -> ast.Module:
        """The module defining the class at ``class_key``. Every gap found is
        added to ``gaps``."""
        self._graph = graph
        self._import_resolver = import_resolver
        self._gaps: list[Gap] = gaps if gaps is not None else []
        self._model: ClassModel = class_model(graph, class_key)
        self._lazy = lazy_imports(graph, class_key)
        self._parts = ModuleParts(
            module_stem=to_module_name(Path(class_key).name), data_files=data_files
        )
        self._unavailable: dict[str, str] = {}

        base: NameImport | None = None
        parent_gap: str | None = None
        try:
            base = base_import(graph, class_key, import_resolver)
        except ConversionGap as exc:
            gap = gap_of(exc, class_key, None)
            self._gaps.append(gap)
            parent_gap = gap.message
        for key, reason in self._model.clashes.items():
            self._unavailable[key] = self._gap_for(key, reason)
        if parent_gap is not None:
            for key in self._model.shapes:
                self._unavailable.setdefault(key, parent_gap)

        body = self._class_body(class_key, base, parent_gap)
        class_def = ast.ClassDef(
            name=self._model.name,
            bases=[ast.Name(id=base.name, ctx=ast.Load())] if base else [],
            keywords=[],
            body=body,
            decorator_list=[],
        )
        results = self._result_classes()
        module_body = [
            *self._build_imports(base),
            *results,
            *self._parts.statements(),
            class_def,
        ]
        module = ast.Module(body=module_body, type_ignores=[])
        return optimize_module(module)

    def _class_body(
        self, class_key: str, base: NameImport | None, parent_gap: str | None
    ) -> list[ast.stmt]:
        graph, model = self._graph, self._model
        fields_resolver = self._import_resolver.scoped(
            graph.class_private_data_key(class_key) or class_key
        )
        spell = type_namer(fields_resolver)
        fields = graph.class_own_fields(class_key)
        if len(fields) != len(model.attributes.fields):
            raise CodeGenError(
                f"The class {self._import_resolver.shown(class_key)} lists "
                f"{len(fields)} private data fields, but its attributes number "
                f"{len(model.attributes.fields)}"
            )
        self._parts.annotation_imports.update(
            annotation_imports([f.type for f in fields], fields_resolver)
        )
        body: list[ast.stmt] = []
        if self.config.include_docstrings:
            body.append(
                ast.Expr(value=ast.Constant(value=f"Class {Path(class_key).name}."))
            )
        body.extend(self._field_annotations(model, fields, spell))
        body.append(self._build_init(model, fields, base is not None, parent_gap))
        body.extend(self._refnum_methods(class_key, fields))

        properties: list[tuple[MethodShape, Accessor]] = []
        methods: list[MethodShape] = []
        for key, shape in model.shapes.items():
            accessor = model.attributes.accessors.get(key)
            if accessor is not None and accessor.kind is AccessorKind.ATTRIBUTE:
                continue
            if accessor is not None and accessor.kind is AccessorKind.PROPERTY:
                properties.append((shape, accessor))
            elif self._has_diagram(key):
                methods.append(shape)
        body.extend(self._build_properties(properties))
        for shape in methods:
            body.append(self._build_method(shape))
        return body

    def _has_diagram(self, key: str) -> bool:
        """Whether the member VI is written as a method: not a polymorphic VI (its
        variants are members of their own) and not one that was not found."""
        graph = self._graph
        return not graph.is_stub(key) and not graph.get_poly_variants(key)

    def _refnum_methods(self, class_key: str, fields: list) -> list[ast.stmt]:
        """``__deepcopy__`` and its helper for a class that holds refnums: a copy
        of an object shares the refnums."""
        own = [
            (attribute, f.type)
            for attribute, f in zip(self._model.attributes.fields, fields, strict=True)
            if holds_refnum(f.type)
        ]
        if not own:
            return []
        parent = self._graph.class_parent_key(class_key)
        self._parts.imports.add(COPY_IMPORT)
        return share_refnums_method(
            own, base_shares=parent is not None and shares_refnums(self._graph, parent)
        )

    def _result_classes(self) -> list[ast.stmt]:
        """The ``NamedTuple`` of each method of the class that returns two or more
        results, defined once."""
        defined: dict[str, ast.stmt] = {}
        for shape in self._model.shapes.values():
            if not declares_result_class(self._model, shape):
                continue
            assert shape.result_class is not None
            if shape.result_class in defined:
                continue
            vi_context = self._graph.get_vi_context(shape.key)
            defined[shape.result_class] = self._result_class_def(shape, vi_context)
        return list(defined.values())

    def _build_imports(self, base: NameImport | None) -> list[ast.stmt]:
        imports: list[ast.stmt] = [
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            )
        ]
        statements = list(_COMMON_IMPORTS)
        if base is not None and base.statement:
            statements.append(base.statement)
        statements.extend(
            result_class_imports(self._graph, self._model, self._import_resolver)
        )
        statements.extend(sorted(self._parts.imports))
        for statement in statements:
            imports.extend(ast.parse(statement).body)
        imports.extend(build_type_checking_block(self._parts.annotation_imports))
        return imports


def build_class_module(
    graph: InMemoryVIGraph,
    class_key: str,
    import_resolver: ImportResolver,
    gaps: list[Gap] | None = None,
    data_files: dict[str, bytes] | None = None,
    config: ClassConfig | None = None,
) -> str:
    """Module source of the class at ``class_key`` (see ``ClassBuilder``)."""
    module = ClassBuilder(config).build_class_module(
        graph, class_key, import_resolver, gaps, data_files
    )
    ast.fix_missing_locations(module)
    return ast.unparse(module)

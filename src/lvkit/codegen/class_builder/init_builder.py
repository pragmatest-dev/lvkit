"""Build the field annotations and ``__init__`` from a class's private data."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.models import ClusterField, LVType, TypeNamer

from ..class_model import ClassModel, class_reference
from ..gaps import raise_statement
from ..import_cycles import imports_inside
from ..type_default import type_default
from ..type_fields import field_annotations

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph

    from ..import_resolver import ImportResolver
    from .shared_state import ModuleParts


class _FieldClasses:
    """Names the classes of a class's private data fields for their defaults in
    ``__init__``, collecting the imports they need."""

    def __init__(self, graph: InMemoryVIGraph, resolver: ImportResolver) -> None:
        self._graph = graph
        self._resolver = resolver
        self.statements: set[str] = set()

    def class_expression(self, lv_type: LVType, what: str) -> str:
        imported = class_reference(self._graph, self._resolver, lv_type, what)
        if imported.statement:
            self.statements.add(imported.statement)
        return imported.name


class _InitBuilderMixin:
    """The attributes a class declares and the ``__init__`` that sets them."""

    if TYPE_CHECKING:
        _graph: InMemoryVIGraph
        _import_resolver: ImportResolver
        _parts: ModuleParts
        _lazy: bool

        def _gap_for(self, key: str, message: str) -> str: ...

    def _field_annotations(
        self,
        model: ClassModel,
        fields: list[ClusterField],
        spell: TypeNamer | None,
    ) -> list[ast.stmt]:
        """One annotated attribute per private data field the class declares."""
        return field_annotations(model.attributes.fields, fields, spell)

    def _field_assignments(
        self,
        model: ClassModel,
        fields: list[ClusterField],
        has_base: bool,
        classes: _FieldClasses,
    ) -> list[ast.stmt]:
        """The base's ``__init__`` call, then each field at its default."""
        body: list[ast.stmt] = []
        if has_base:
            body.append(ast.parse("super().__init__()").body[0])
        for attribute, field in zip(model.attributes.fields, fields, strict=True):
            body.append(
                ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=attribute,
                            ctx=ast.Store(),
                        )
                    ],
                    value=type_default(classes, field.type, "A private data field of"),
                )
            )
        return body

    def _build_init(
        self,
        model: ClassModel,
        fields: list[ClusterField],
        has_base: bool,
        unavailable: str | None,
    ) -> ast.FunctionDef:
        """``__init__``: the base's, then each field the class declares at its
        default; ``unavailable`` (a gap) makes it raise instead."""
        classes = _FieldClasses(self._graph, self._import_resolver)
        body: list[ast.stmt] = []
        if unavailable is not None:
            body.append(raise_statement(unavailable))
        else:
            try:
                body = self._field_assignments(model, fields, has_base, classes)
            except ConversionGap as exc:
                classes.statements.clear()
                body = [raise_statement(self._gap_for(model.key, str(exc)))]
        if classes.statements:
            init_imports = classes.statements
            if self._lazy:
                body, init_imports = imports_inside(body, init_imports)
            self._parts.imports.update(init_imports)
        return ast.FunctionDef(
            name="__init__",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="self", annotation=None)],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=body or [ast.Pass()],
            decorator_list=[],
            returns=ast.Constant(value=None),
        )

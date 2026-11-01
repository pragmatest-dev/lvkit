"""The annotated fields of a generated class or typedef cluster, and the imports
their types need."""

from __future__ import annotations

import ast

from lvkit.models import ClusterField, LVType, TypeNamer

from .ast_utils import parse_expr
from .import_resolver import ImportResolver

# What the Python spelling of a field type or an alias can name.
TYPING_IMPORTS = {"from pathlib import Path", "from typing import Any"}


def annotation_imports(
    lv_types: list[LVType | None], import_resolver: ImportResolver
) -> set[str]:
    """Imports of the classes and typedefs that ``lv_types`` name."""
    imports: set[str] = set()
    for lv_type in lv_types:
        if lv_type is None:
            continue
        for named in lv_type.named_types():
            type_ref = named.type_ref()
            imported = import_resolver.type_import(type_ref) if type_ref else None
            if imported is not None and imported.statement:
                imports.add(imported.statement)
    return imports


def field_annotations(
    names: list[str], fields: list[ClusterField], spell: TypeNamer | None
) -> list[ast.stmt]:
    """One annotated attribute per field, named ``names[i]``."""
    annotations: list[ast.stmt] = []
    for field, name in zip(fields, names, strict=True):
        annotation = field.type.to_python(spell) if field.type is not None else "Any"
        annotations.append(
            ast.AnnAssign(
                target=ast.Name(id=name, ctx=ast.Store()),
                annotation=parse_expr(annotation),
                simple=1,
            )
        )
    return annotations

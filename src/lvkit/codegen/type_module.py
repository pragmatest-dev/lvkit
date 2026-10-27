"""Python module for a LabVIEW class or a typedef control.

Every class and typedef a generated VI names -- in an annotation, or as the enum
a constant is a member of -- is defined in the module generated here, and the
VI imports it from there:

* An enum or ring typedef is an ``IntEnum`` with the control's items.
* A cluster typedef is a ``SimpleNamespace`` subclass annotated with its fields.
* A class descends from its parent class (a root class is a plain class) and is
  annotated with its private data.
* Any other typedef is an alias of the type it defines.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.models import ClusterField, LVType, LVTypeKind, TypeNamer
from lvkit.type_names import derive_python_name, is_enum_member_name

from .ast_utils import build_type_checking_block, parse_expr, to_var_name
from .context import type_namer
from .gaps import Gap, gap_of, raise_statement
from .import_resolver import ImportResolver, NameImport

# What the Python spelling of a field type or an alias can name.
_TYPING_IMPORTS = {"from pathlib import Path", "from typing import Any"}


def build_type_module(
    graph: InMemoryVIGraph,
    key: str,
    import_resolver: ImportResolver,
    gaps: list[Gap] | None = None,
) -> str:
    """Module source defining the class or typedef at dependency key ``key``. A
    definition that cannot be written is a gap: the module raises where the
    definition would run, and the gap is added to ``gaps``."""
    runtime_imports: set[str] = set()
    annotation_imports: set[str] = set()
    try:
        if graph.is_class(key):
            runtime_imports, annotation_imports, definition = _class_definition(
                graph, key, import_resolver
            )
        else:
            runtime_imports, annotation_imports, definition = _typedef_definition(
                graph, key, import_resolver
            )
    except ConversionGap as exc:
        gap = gap_of(exc, key, None)
        if gaps is not None:
            gaps.append(gap)
        runtime_imports, annotation_imports = set(), set()
        definition = [raise_statement(gap.reason)]
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
    ]
    for statement in sorted(runtime_imports):
        body.extend(ast.parse(statement).body)
    body.extend(build_type_checking_block(annotation_imports))
    body.extend(definition)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def _annotation_imports(
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
            if imported is not None:
                imports.add(imported.statement)
    return imports


def _field_annotations(
    fields: list[ClusterField], spell: TypeNamer | None
) -> list[ast.stmt]:
    """One annotated attribute per field, named as ``Unbundle By Name`` reads it."""
    annotations: list[ast.stmt] = []
    taken: set[str] = set()
    for field in fields:
        name = to_var_name(field.name)
        if name in taken:
            continue
        taken.add(name)
        annotation = (
            field.type.to_python(spell) if field.type is not None else "Any"
        )
        annotations.append(
            ast.AnnAssign(
                target=ast.Name(id=name, ctx=ast.Store()),
                annotation=parse_expr(annotation),
                simple=1,
            )
        )
    return annotations


def _class_definition(
    graph: InMemoryVIGraph, key: str, import_resolver: ImportResolver
) -> tuple[set[str], set[str], list[ast.stmt]]:
    name = derive_python_name(Path(key).name)
    parent_key = graph.class_parent_key(key)
    parent: NameImport | None = None
    if parent_key is not None:
        parent = import_resolver.node_import(parent_key)
    elif graph.class_parent_name(key) is not None:
        raise ConversionGap(
            f"Parent class {graph.class_parent_name(key)} of {key} is not loaded"
        )
    fields = graph.class_own_fields(key)
    body: list[ast.stmt] = [
        ast.Expr(value=ast.Constant(value=f"Class {Path(key).name}.")),
        *_field_annotations(fields, type_namer(import_resolver)),
    ]
    definition = ast.ClassDef(
        name=name,
        bases=[ast.Name(id=parent.name, ctx=ast.Load())] if parent else [],
        keywords=[],
        body=body,
        decorator_list=[],
    )
    return (
        {*([parent.statement] if parent else []), *_TYPING_IMPORTS},
        _annotation_imports([f.type for f in fields], import_resolver),
        [definition],
    )


def _typedef_definition(
    graph: InMemoryVIGraph, key: str, import_resolver: ImportResolver
) -> tuple[set[str], set[str], list[ast.stmt]]:
    info = graph.get_typedef(key)
    name = derive_python_name(info.name)
    root = info.root_type
    if root.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return (
            {"from enum import IntEnum"},
            set(),
            [_enum_definition(name, info.name, root)],
        )
    if root.kind == LVTypeKind.CLUSTER and root.fields:
        body: list[ast.stmt] = [
            ast.Expr(value=ast.Constant(value=f"LabVIEW typedef {info.name}.")),
            *_field_annotations(root.fields, type_namer(import_resolver)),
        ]
        definition = ast.ClassDef(
            name=name,
            bases=[ast.Name(id="SimpleNamespace", ctx=ast.Load())],
            keywords=[],
            body=body,
            decorator_list=[],
        )
        return (
            {"from types import SimpleNamespace", *_TYPING_IMPORTS},
            _annotation_imports([f.type for f in root.fields], import_resolver),
            [definition],
        )
    alias = ast.Assign(
        targets=[ast.Name(id=name, ctx=ast.Store())],
        value=parse_expr(root.to_python(type_namer(import_resolver))),
    )
    runtime = _annotation_imports([root], import_resolver) | _TYPING_IMPORTS
    return runtime, set(), [alias]


def _enum_definition(name: str, label: str, root: LVType) -> ast.ClassDef:
    body: list[ast.stmt] = [
        ast.Expr(value=ast.Constant(value=f"LabVIEW enum {label}."))
    ]
    for member, item in (root.values or {}).items():
        if is_enum_member_name(member):
            body.append(
                ast.Assign(
                    targets=[ast.Name(id=member, ctx=ast.Store())],
                    value=ast.Constant(value=item.value),
                )
            )
    return ast.ClassDef(
        name=name,
        bases=[ast.Name(id="IntEnum", ctx=ast.Load())],
        keywords=[],
        body=body,
        decorator_list=[],
    )

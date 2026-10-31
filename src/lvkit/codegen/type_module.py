"""Python module for a LabVIEW class or a typedef control.

Every class and typedef a generated VI names -- in an annotation, or as the enum
a constant is a member of -- is defined in the module generated here, and the
VI imports it from there:

* An enum or ring typedef is an ``IntEnum`` with the control's items.
* A cluster typedef is a ``SimpleNamespace`` subclass annotated with its fields.
* A class is the one Python class of the LabVIEW class (``class_builder``): its
  methods are the class's VIs.
* Any other typedef is an alias of the type it defines.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph import InMemoryVIGraph
from lvkit.models import LVType, LVTypeKind
from lvkit.type_names import derive_python_name, is_enum_member_name

from .ast_utils import (
    build_type_checking_block,
    field_attribute_names,
    parse_expr,
)
from .class_builder import build_class_module
from .context import type_namer
from .gaps import Gap, gap_of, raise_statement
from .import_resolver import ImportResolver
from .type_fields import TYPING_IMPORTS, annotation_imports, field_annotations
from .value_copy import COPY_IMPORT, holds_refnum, share_refnums_method


def build_type_module(
    graph: InMemoryVIGraph,
    key: str,
    import_resolver: ImportResolver,
    gaps: list[Gap] | None = None,
    data_files: dict[str, bytes] | None = None,
) -> str:
    """Module source defining the class or typedef at dependency key ``key``. A
    definition that cannot be written is a gap: the module raises where the
    definition would run, and the gap is added to ``gaps``. The files a class's
    methods read at run time are added to ``data_files``."""
    if graph.is_class(key):
        return build_class_module(graph, key, import_resolver, gaps, data_files)
    try:
        runtime_imports, annotated_imports, definition = _typedef_definition(
            graph, key, import_resolver
        )
    except ConversionGap as exc:
        gap = gap_of(exc, key, None)
        if gaps is not None:
            gaps.append(gap)
        runtime_imports, annotated_imports = set(), set()
        definition = [raise_statement(gap.message)]
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        )
    ]
    for statement in sorted(runtime_imports):
        body.extend(ast.parse(statement).body)
    body.extend(build_type_checking_block(annotated_imports))
    body.extend(definition)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)


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
            *field_annotations(
                field_attribute_names(f.name for f in root.fields),
                root.fields,
                type_namer(import_resolver),
            ),
        ]
        names = field_attribute_names(f.name for f in root.fields)
        refnums = [
            (attribute, f.type)
            for attribute, f in zip(names, root.fields, strict=True)
            if holds_refnum(f.type)
        ]
        imports = {"from types import SimpleNamespace", *TYPING_IMPORTS}
        if refnums:
            body.extend(share_refnums_method(refnums, base_shares=False))
            imports.add(COPY_IMPORT)
        definition = ast.ClassDef(
            name=name,
            bases=[ast.Name(id="SimpleNamespace", ctx=ast.Load())],
            keywords=[],
            body=body,
            decorator_list=[],
        )
        return (
            imports,
            annotation_imports([f.type for f in root.fields], import_resolver),
            [definition],
        )
    alias = ast.Assign(
        targets=[ast.Name(id=name, ctx=ast.Store())],
        value=parse_expr(root.to_python(type_namer(import_resolver))),
    )
    runtime = annotation_imports([root], import_resolver) | TYPING_IMPORTS
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

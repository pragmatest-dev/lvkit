"""How generated code reaches a member of a cluster value.

An on-diagram anonymous cluster (no typedef, no class) is a Python tuple read by
position; a named or typedef cluster and a class object keep attribute access.
The field index a Bundle/Unbundle By Name stores counts the fields depth first
(``_flatten_fields``); ``member_expression`` gives the read of one top-level
member for code that holds the cluster's type (the Event Data Node of a user
event, the members of a cluster row of Register For Events).
"""

from __future__ import annotations

from lvkit.models import ClusterField, LVType, LVTypeKind, TypeResolutionNeeded

from .ast_utils import field_attribute_paths
from .class_attributes import ClassAttributes, class_field_paths
from .class_model import type_attributes
from .context import CodeGenContext


def is_anonymous_cluster(lv_type: LVType | None) -> bool:
    """True for an on-diagram anonymous cluster — no typedef and no class, so
    its fields are positional placeholders (e.g. ``field_1``) with no meaningful
    names. Such a cluster is represented as a Python tuple accessed by position;
    a named/typedef cluster keeps attribute access. See
    docs/_internal/design/codegen-fix-assumptions.md."""
    return (
        lv_type is not None
        and lv_type.kind == LVTypeKind.CLUSTER
        and not lv_type.typedef_name
        and not lv_type.classname
    )


def position_path(
    index: int | None, fields: list[ClusterField] | None, context: str
) -> tuple[int, ...]:
    """Map a depth-first field index, including parents, to tuple positions.

    Descendants of named types are not positional; keep their indices in the
    traversal but reject a selected path through that representation boundary.
    """
    paths: list[tuple[int, ...] | None] = []

    def visit(
        current: list[ClusterField], prefix: tuple[int, ...], positional: bool
    ) -> None:
        for position, field in enumerate(current):
            path = prefix + (position,)
            paths.append(path if positional else None)
            if field.type and field.type.fields:
                visit(
                    field.type.fields,
                    path,
                    positional and is_anonymous_cluster(field.type),
                )

    visit(fields or [], (), True)
    if index is not None and 0 <= index < len(paths):
        path = paths[index]
        if path is not None:
            return path
    raise TypeResolutionNeeded(type_name=f"positional field[{index}]", context=context)


def attribute_paths(
    class_fields: list[ClusterField], class_attrs: ClassAttributes | None
) -> list[list[str]]:
    """The attribute path of every field of the aggregate: the attributes the
    class names its private data fields, the field names of any other cluster."""
    if class_attrs is not None:
        return class_field_paths(class_attrs, class_fields)
    return field_attribute_paths(class_fields)


def flat_index(fields: list[ClusterField], member: int) -> int:
    """The depth-first index of the top-level field ``member``."""

    def size(field: ClusterField) -> int:
        inner = field.type.fields if field.type and field.type.fields else []
        return 1 + sum(size(f) for f in inner)

    return sum(size(f) for f in fields[:member])


def member_expression(
    ctx: CodeGenContext, lv_type: LVType, member: int, value: str, context: str
) -> str:
    """The expression reading the top-level field ``member`` (its position among
    the fields) of the cluster ``value`` of type ``lv_type``."""
    fields = ctx.graph.get_type_fields(lv_type) if ctx.graph is not None else None
    fields = fields or lv_type.fields
    if not fields or not 0 <= member < len(fields):
        raise TypeResolutionNeeded(type_name=f"field[{member}]", context=context)
    index = flat_index(fields, member)
    if is_anonymous_cluster(lv_type):
        path = position_path(index, fields, context)
        return f"({value})" + "".join(f"[{position}]" for position in path)
    attributes = (
        type_attributes(ctx.graph, lv_type, ctx.vi_name, "Event Data Node")
        if ctx.graph is not None
        else None
    )
    paths = attribute_paths(fields, attributes)
    if index >= len(paths):
        raise TypeResolutionNeeded(type_name=f"field[{member}]", context=context)
    return f"{value}.{'.'.join(paths[index])}"

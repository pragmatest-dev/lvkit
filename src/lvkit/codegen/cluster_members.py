"""How generated code reaches a member of a cluster value.

An on-diagram anonymous cluster (no typedef, no class) is a Python tuple read by
position; a named or typedef cluster and a class object keep attribute access;
each level of a nested cluster takes the form its own cluster has
(``member_paths``). The field index a Bundle/Unbundle By Name stores counts the
fields depth first (``_flatten_fields``); the one a classic Bundle or Unbundle
stores is a position among the cluster's own fields (``field_slot``).
``member_expression`` gives the read of one top-level member for code that holds
the cluster's type (the Event Data Node of a user event, the members of a cluster
row of Register For Events).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.models import ClusterField, LVType, LVTypeKind, TypeResolutionNeeded
from lvkit.parser.constants import NODE_CLASS_DEMUX, NODE_CLASS_MUX

from .ast_utils import field_attribute_paths
from .class_attributes import ClassAttributes, class_field_paths
from .class_model import type_attributes
from .context import CodeGenContext
from .native_array import replaced_field

MemberStep = int | str
"""One level of the way to a cluster member: a position in the tuple of an
anonymous cluster, or the attribute name of a named, typedef or class cluster."""


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


@dataclass(frozen=True)
class ClusterShape:
    """What a Bundle or Unbundle knows of its cluster: the wire's type, the fields
    that type has, and the attributes a class names its private data."""

    lv_type: LVType | None
    fields: list[ClusterField] | None
    class_attrs: ClassAttributes | None = None


def member_paths(
    lv_type: LVType | None,
    fields: list[ClusterField],
    class_attrs: ClassAttributes | None,
) -> list[tuple[MemberStep, ...]]:
    """The way to every field slot of the cluster ``lv_type`` (depth first, a
    cluster field being a slot of its own followed by its fields): one step per
    level, a position in the tuple of an anonymous cluster or the attribute name
    of a named, typedef or class cluster, each level taking the form its own
    cluster has. ``class_attrs`` gives the attributes a class names its private
    data."""
    names = attribute_paths(fields, class_attrs)
    paths: list[tuple[MemberStep, ...]] = []

    def visit(
        current: list[ClusterField], prefix: tuple[MemberStep, ...], anonymous: bool
    ) -> None:
        for position, field in enumerate(current):
            slot = names[len(paths)]
            step: MemberStep = position if anonymous else slot[len(prefix)]
            path = prefix + (step,)
            paths.append(path)
            if field.type and field.type.fields:
                visit(field.type.fields, path, is_anonymous_cluster(field.type))

    visit(fields, (), is_anonymous_cluster(lv_type))
    return paths


def read_member(base: str, steps: tuple[MemberStep, ...]) -> str:
    """The expression reading the member ``steps`` lead to from the cluster
    ``base``."""
    expression = f"({base})" if isinstance(steps[0], int) else base
    for step in steps:
        expression += f"[{step}]" if isinstance(step, int) else f".{step}"
    return expression


def needs_copy(steps: tuple[MemberStep, ...]) -> bool:
    """Whether ``write_member`` copies a named cluster inside the one it writes
    into (so ``copy`` is imported)."""
    return any(isinstance(step, str) for step in steps[1:])


def write_member(
    ctx: CodeGenContext, location: str, steps: tuple[MemberStep, ...], value: str
) -> list[ast.stmt]:
    """The statements that make ``location`` (a variable, or an attribute of one)
    hold its cluster with the member ``steps`` lead to set to ``value``. A tuple
    is rebuilt with the member replaced; a named cluster below the one written
    into is copied first, so another reader of the incoming value keeps its own;
    the named cluster ``location`` itself is the caller's own."""
    if all(isinstance(step, int) for step in steps):
        positions = tuple(step for step in steps if isinstance(step, int))
        return _statements(f"{location} = {replaced_field(location, positions, value)}")
    step, *tail = steps
    if isinstance(step, int):
        part = ctx.claim_name("cluster_part")
        statements = _statements(f"{part} = {_copy_of(f'{location}[{step}]', tail)}")
        statements += write_member(ctx, part, tuple(tail), value)
        statements += _statements(
            f"{location} = {location}[:{step}] + ({part},) + {location}[{step + 1}:]"
        )
        return statements
    member = f"{location}.{step}"
    if not tail:
        return _statements(f"{member} = {value}")
    statements = (
        _statements(f"{member} = copy.copy({member})")
        if (isinstance(tail[0], str))
        else []
    )
    return statements + write_member(ctx, member, tuple(tail), value)


def _copy_of(member: str, tail: list[MemberStep]) -> str:
    """``member`` as the cluster ``tail`` writes into: a named cluster is copied,
    a tuple is rebuilt by the write itself."""
    return f"copy.copy({member})" if isinstance(tail[0], str) else member


def _statements(source: str) -> list[ast.stmt]:
    return ast.parse(source).body


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


def member_path(
    index: int | None, shape: ClusterShape
) -> tuple[MemberStep, ...] | None:
    """The way to the field slot ``index`` (depth first) of the cluster; None when
    the cluster has no such slot."""
    if index is None or not shape.fields:
        return None
    paths = member_paths(shape.lv_type, shape.fields, shape.class_attrs)
    return paths[index] if 0 <= index < len(paths) else None


def field_slot(node_type: str | None, index: int, fields: list[ClusterField]) -> int:
    """The depth-first slot a Bundle/Unbundle element selects. An element of a By
    Name node stores its slot; an element of a classic Bundle or Unbundle (a
    ``mux`` or ``demux``) stores its position among the cluster's own fields, a
    cluster field before it counting once."""
    if node_type in (NODE_CLASS_MUX, NODE_CLASS_DEMUX) and 0 <= index < len(fields):
        return flat_index(fields, index)
    return index


def member_expression(
    ctx: CodeGenContext, lv_type: LVType, member: int, value: str
) -> str:
    """The expression reading the top-level field ``member`` (its position among
    the fields) of the cluster ``value`` of type ``lv_type``."""
    fields = ctx.graph.get_type_fields(lv_type) if ctx.graph is not None else None
    fields = fields or lv_type.fields
    if not fields or not 0 <= member < len(fields):
        raise TypeResolutionNeeded(type_name=f"field[{member}]")
    attributes = (
        type_attributes(ctx.graph, lv_type, ctx.vi_name, "Event Data Node")
        if ctx.graph is not None
        else None
    )
    path = member_path(
        flat_index(fields, member), ClusterShape(lv_type, fields, attributes)
    )
    if path is None:
        raise TypeResolutionNeeded(type_name=f"field[{member}]")
    return read_member(value, path)

"""Copies for the values LabVIEW passes by value that Python passes by reference.

A class object or a named cluster (a typedef cluster) is a value in LabVIEW: when
its wire forks, each branch has its own. In Python the generated code changes
such an object where it is (Bundle By Name, an In Place Element Structure, a
setter, a method called on it, a VI that takes it). So a node that changes one
in place works on a copy when the graph says something else can still read the
value (``InMemoryVIGraph.value_is_shared``).

A copy is one level, ``copy.copy``: a new object holding the same field values.
A field that holds a reference (a queue, a Data Value Reference, a file, a VI
reference) keeps the same object, so the copies share the reference as they do
in LabVIEW, and a field that holds a value changed on a copy leaves the original
as it was. The objects inside the copy (a named cluster in a field) are the
original's, so a write that goes through them, ``obj.a.b = value``, first copies
each container on its path (``own_path``): a write never changes an object
another value can still reach."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.lv_type_codes import is_variant_type_name
from lvkit.models import LVType, LVTypeKind, Terminal

from .ast_utils import COPY_IMPORT, build_assign, parse_expr, to_var_name
from .variant_values import is_coerced

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode

    from .context import CodeGenContext


def is_by_value_object(lv_type: LVType | None) -> bool:
    """Whether a wire of this type carries an object that generated code changes
    in place: a class object, a named cluster, or a ``Variant``."""
    if lv_type is None:
        return False
    if lv_type.ref_type == "UDClassInst" or is_variant_type_name(
        lv_type.underlying_type
    ):
        return True
    return (
        lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF)
        and lv_type.typedef_name is not None
        and bool(lv_type.fields)
    )


def owned_input(
    ctx: CodeGenContext,
    term: Terminal,
    node: AnyGraphNode,
    statements: list[ast.stmt],
) -> str | None:
    """The expression feeding the input ``term`` of ``node`` when ``node`` changes
    the object in place: the wire's own value, or, when something else can still
    read that value, a copy made in ``statements`` first. A ``Variant`` just made
    for a typed wire is nobody else's, so it is never copied."""
    value = ctx.wired_input(term.id, node)
    if (
        value is None
        or ctx.graph is None
        or not is_by_value_object(term.lv_type)
        or is_coerced(ctx, term.id)
        or not ctx.graph.value_is_shared(term.id)
    ):
        return value
    base = value if value.isidentifier() else "value"
    copy_var = ctx.make_output_var(f"{to_var_name(base)}_copy", node.id)
    ctx.add_import(COPY_IMPORT)
    statements.append(build_assign(copy_var, parse_expr(f"copy.copy({value})")))
    return copy_var


def own_path(root: str, path: list[str]) -> list[ast.stmt]:
    """The statements that make each container on the attribute ``path`` below
    ``root`` the object's own before a write to ``root.<path>``: for the path
    ``a.b.c`` they are ``root.a = copy.copy(root.a)`` and ``root.a.b =
    copy.copy(root.a.b)``. A write of one attribute needs none."""
    return [
        ast.parse(f"{owner} = copy.copy({owner})").body[0]
        for owner in (
            f"{root}.{'.'.join(path[:depth])}" for depth in range(1, len(path))
        )
    ]

"""Element-wise arithmetic over clusters, written out field by field.

LabVIEW's arithmetic functions take "numeric scalar OR array [ numeric type ] OR
cluster [ numeric types ]". Given two clusters of the same structure the function
works on the respective elements, with one cluster and a scalar the scalar meets
every element, and a one-input function works on each element (NI, "Polymorphic
Functions", Polymorphism for Numeric Functions)::

    a - b       ->  (a[0] - b[0], a[1] - b[1])                      anonymous cluster
    a - b       ->  SimpleNamespace(x=a.x - b.x, y=a.y - b.y)       typedef cluster
    a + 1       ->  SimpleNamespace(x=a.x + 1, y=a.y + 1)

The result takes the form its wire's cluster has (``cluster_members``): a tuple for
an on-diagram cluster, the attribute object a typedef default makes for a typedef.
A connected chain of operators becomes one cluster, not one per operator. An
operand is a cluster when the graph types its wire as one; comparison functions
are not rewritten (they compare the cluster as one value by default).
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping

from lvkit.models import ClusterField, LVType, LVTypeKind

from .ast_utils import field_attribute_names
from .cluster_members import MemberStep, is_anonymous_cluster
from .elementwise import arrayify, unary_call

FieldsOf = Callable[[LVType], list[ClusterField] | None]


def can_hold_arithmetic(lv_type: LVType) -> bool:
    """True for a cluster whose members the generated code reads by position or
    attribute: an on-diagram or typedef cluster, not a class object."""
    return lv_type.kind == LVTypeKind.CLUSTER and not lv_type.classname


def _operands_of(node: ast.expr) -> list[ast.expr] | None:
    if isinstance(node, ast.BinOp):
        return [node.left, node.right]
    if isinstance(node, ast.UnaryOp):
        return [node.operand]
    if isinstance(node, ast.Call) and unary_call(node):
        return list(node.args)
    return None


def _reads(node: ast.expr, clusters: Mapping[str, LVType]) -> bool:
    """True when ``node`` is, or is built by operators from, a cluster operand."""
    if ast.unparse(node) in clusters:
        return True
    children = _operands_of(node)
    return children is not None and any(_reads(c, clusters) for c in children)


def _step(base: ast.expr, step: MemberStep) -> ast.expr:
    if isinstance(step, int):
        return ast.Subscript(value=base, slice=ast.Constant(value=step))
    return ast.Attribute(value=base, attr=step, ctx=ast.Load())


class _Builder:
    def __init__(self, clusters: Mapping[str, LVType], fields_of: FieldsOf) -> None:
        self.clusters = clusters
        self.fields_of = fields_of
        self.ranks: dict[str, int] = {}

    def at(
        self, node: ast.expr, path: tuple[MemberStep, ...], rank: int = 0
    ) -> ast.expr:
        """The member at ``path`` of the cluster the operator expression ``node``
        computes: the operators applied to the respective members; an operand that
        is not a cluster meets every member as it is. A member that is an array
        of ``rank`` dimensions is recorded in ``self.ranks`` under its read."""
        text = ast.unparse(node)
        if text in self.clusters:
            read: ast.expr = ast.parse(text, mode="eval").body
            for step in path:
                read = _step(read, step)
            if rank:
                self.ranks[ast.unparse(read)] = rank
            return read
        children = _operands_of(node)
        if children is None or not _reads(node, self.clusters):
            return node
        members = [self.at(child, path, rank) for child in children]
        if isinstance(node, ast.BinOp):
            return ast.BinOp(left=members[0], op=node.op, right=members[1])
        if isinstance(node, ast.UnaryOp):
            return ast.UnaryOp(op=node.op, operand=members[0])
        assert isinstance(node, ast.Call)
        return ast.Call(func=node.func, args=members, keywords=[])

    def build(
        self, node: ast.expr, lv_type: LVType, path: tuple[MemberStep, ...]
    ) -> ast.expr:
        """The cluster ``lv_type`` whose every member is the operators applied to
        the respective members of the operands."""
        fields = self.fields_of(lv_type) or []
        anonymous = is_anonymous_cluster(lv_type)
        names = field_attribute_names(f.name for f in fields)
        members: list[ast.expr] = []
        for position, (field, name) in enumerate(zip(fields, names, strict=True)):
            sub = path + (position if anonymous else name,)
            if field.type is not None and can_hold_arithmetic(field.type):
                members.append(self.build(node, field.type, sub))
            elif field.type is not None and field.type.kind == LVTypeKind.ARRAY:
                self.ranks = {}
                member = self.at(node, sub, field.type.dimensions or 1)
                members.append(arrayify(member, self.ranks)[0])
            else:
                members.append(self.at(node, sub))
        if anonymous:
            return ast.Tuple(elts=members, ctx=ast.Load())
        return ast.Call(
            func=ast.Name(id="SimpleNamespace", ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(arg=name, value=member)
                for name, member in zip(names, members, strict=True)
            ],
        )


def clusterify(
    expr: ast.expr, clusters: Mapping[str, LVType], fields_of: FieldsOf
) -> tuple[ast.expr, bool]:
    """Rewrite one primitive's expression so its operators apply per member of
    the cluster operands. ``clusters`` maps the source text of each cluster operand
    to its type; ``fields_of`` gives the fields of a cluster type. Returns
    (expression, whether anything was rewritten)."""
    if not clusters or _operands_of(expr) is None or not _reads(expr, clusters):
        return expr, False
    shape = _shape(expr, clusters)
    new = _Builder(clusters, fields_of).build(expr, shape, ())
    ast.fix_missing_locations(new)
    return new, True


def _shape(expr: ast.expr, clusters: Mapping[str, LVType]) -> LVType:
    """The cluster type of the first cluster operand under ``expr``."""
    text = ast.unparse(expr)
    if text in clusters:
        return clusters[text]
    for child in _operands_of(expr) or []:
        if _reads(child, clusters):
            return _shape(child, clusters)
    raise AssertionError("an expression that reads a cluster operand has a shape")

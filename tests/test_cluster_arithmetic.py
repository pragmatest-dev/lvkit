"""Arithmetic on clusters applies to the respective members, in the cluster's form."""

from __future__ import annotations

import ast
from types import SimpleNamespace

from lvkit.codegen.ast_utils import parse_expr
from lvkit.codegen.cluster_arithmetic import clusterify
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.primitive_resolver import PrimitiveResolver

_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _cluster(typedef: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef,
        fields=[
            ClusterField(name="Horizontal", type=_I32),
            ClusterField(name="Vertical", type=_I32),
        ],
    )


def _run(source: str, lv_type: LVType, **values):
    clusters = {
        name: lv_type
        for name in values
        if isinstance(values[name], tuple | SimpleNamespace)
    }
    new, rewritten = clusterify(parse_expr(source), clusters, lambda t: t.fields)
    assert rewritten
    return eval(ast.unparse(new), {"SimpleNamespace": SimpleNamespace}, values)


def test_subtract_of_two_typedef_clusters_is_a_typedef_cluster():
    a = SimpleNamespace(horizontal=10, vertical=20)
    b = SimpleNamespace(horizontal=3, vertical=5)
    result = _run("a - b", _cluster("Point.ctl"), a=a, b=b)
    assert result == SimpleNamespace(horizontal=7, vertical=15)


def test_add_of_a_typedef_cluster_and_a_scalar_adds_it_to_every_member():
    a = SimpleNamespace(horizontal=10, vertical=20)
    result = _run("a + 1", _cluster("Point.ctl"), a=a)
    assert result == SimpleNamespace(horizontal=11, vertical=21)
    assert _run("100 - a", _cluster("Point.ctl"), a=a) == SimpleNamespace(
        horizontal=90, vertical=80
    )


def test_a_chain_of_operators_is_one_cluster():
    a = SimpleNamespace(horizontal=10, vertical=20)
    b = SimpleNamespace(horizontal=3, vertical=5)
    result = _run("-(a - b) * 2", _cluster("Point.ctl"), a=a, b=b)
    assert result == SimpleNamespace(horizontal=-14, vertical=-30)


def test_quotient_and_absolute_value_apply_to_every_member():
    a = SimpleNamespace(horizontal=-95, vertical=47)
    assert _run("a // 10", _cluster("Point.ctl"), a=a) == SimpleNamespace(
        horizontal=-10, vertical=4
    )
    assert _run("abs(a)", _cluster("Point.ctl"), a=a) == SimpleNamespace(
        horizontal=95, vertical=47
    )


def test_quotient_and_absolute_value_are_element_wise_primitives():
    resolver = PrimitiveResolver()
    assert (resolver.get_by_id(1056) or {}).get("elementwise")
    assert (resolver.get_by_id(1054) or {}).get("elementwise")


def test_plain_cluster_stays_a_tuple():
    result = _run("a - b", _cluster(None), a=(10, 20), b=(3, 5))
    assert result == (7, 15)


def test_scalar_expressions_are_unchanged():
    new, rewritten = clusterify(parse_expr("a - b"), {}, lambda t: t.fields)
    assert not rewritten
    assert ast.unparse(new) == "a - b"

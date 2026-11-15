"""Fields whose labels become one Python name each get their own attribute, in
every place generated code names them: class data, Bundle/Unbundle By Name, and
cluster constants."""

from __future__ import annotations

import ast
import copy
from types import SimpleNamespace

from lvkit.codegen.ast_utils import field_attribute_names, field_attribute_paths
from lvkit.codegen.cluster_members import (
    ClusterShape,
    member_path,
    read_member,
    write_member,
)
from lvkit.codegen.context import _format_cluster_constant
from lvkit.codegen.type_fields import field_annotations
from lvkit.graph.models import ConstantNode
from lvkit.models import ClusterField, LVType, LVTypeKind

_INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="I32")
_NAMED = LVType(kind=LVTypeKind.CLUSTER, typedef_name="T.ctl")


def _cluster(*labels: str) -> list[ClusterField]:
    return [ClusterField(name=label, type=_INT) for label in labels]


def _nested() -> list[ClusterField]:
    inner = LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Inner.ctl",
        fields=_cluster("Count", "count"),
    )
    return [
        ClusterField(name="Count", type=_INT),
        ClusterField(name="count", type=inner),
    ]


def test_one_cluster_level_gets_distinct_names() -> None:
    assert field_attribute_names(["Count", "count", "x"]) == ["count", "count_2", "x"]


def test_paths_follow_the_depth_first_field_slots() -> None:
    assert field_attribute_paths(_nested()) == [
        ["count"],
        ["count_2"],
        ["count_2", "count"],
        ["count_2", "count_2"],
    ]


def _path(index: int, fields: list[ClusterField]) -> tuple[int | str, ...]:
    path = member_path(index, ClusterShape(_NAMED, fields))
    assert path is not None
    return path


def test_unbundle_and_bundle_by_name_use_the_distinct_names() -> None:
    fields = _nested()
    expressions = [read_member("data", _path(i, fields)) for i in range(4)]
    assert expressions == [
        "data.count",
        "data.count_2",
        "data.count_2.count",
        "data.count_2.count_2",
    ]
    assert [_path(i, fields) for i in range(4)] == [
        ("count",),
        ("count_2",),
        ("count_2", "count"),
        ("count_2", "count_2"),
    ]


def test_bundle_by_name_into_a_nested_field_sets_that_field_only() -> None:
    fields = _nested()
    for index, expected in [(2, (1, 9, 3)), (3, (1, 2, 9)), (0, (9, 2, 3))]:
        data = SimpleNamespace(count=1, count_2=SimpleNamespace(count=2, count_2=3))
        statements = write_member(None, "data", _path(index, fields), "9")  # type: ignore[arg-type]
        exec(  # noqa: S102
            ast.unparse(ast.Module(body=statements, type_ignores=[])),
            {"data": data, "copy": copy},
        )
        assert (data.count, data.count_2.count, data.count_2.count_2) == expected


def test_a_cluster_constant_with_repeated_names_runs() -> None:
    lv_type = LVType(
        kind=LVTypeKind.CLUSTER, fields=_cluster("Count", "count"), typedef_name="T.ctl"
    )
    constant = ConstantNode(
        id="c", vi_path="v.vi", value="{'Count': 1, 'count': 2}", lv_type=lv_type
    )
    expression = _format_cluster_constant(constant)
    assert expression == "SimpleNamespace(count=1, count_2=2)"
    value = eval(expression, {"SimpleNamespace": SimpleNamespace})  # noqa: S307
    assert (value.count, value.count_2) == (1, 2)


def test_a_class_or_typedef_module_annotates_every_field_it_names() -> None:
    fields = _cluster("Count", "count", "x")
    annotations = field_annotations(
        field_attribute_names(f.name for f in fields), fields, None
    )
    assert [a.target.id for a in annotations] == [  # type: ignore[attr-defined]
        "count",
        "count_2",
        "x",
    ]

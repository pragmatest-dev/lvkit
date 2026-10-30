"""Fields whose labels become one Python name each get their own attribute, in
every place generated code names them: class data, Bundle/Unbundle By Name, and
cluster constants."""

from __future__ import annotations

from types import SimpleNamespace

from lvkit.codegen.ast_utils import field_attribute_names, field_attribute_paths
from lvkit.codegen.context import _format_cluster_constant
from lvkit.codegen.nodes.nmux import _field_expr, _field_name
from lvkit.graph.models import ConstantNode
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal

_INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="I32")


def _cluster(*labels: str) -> list[ClusterField]:
    return [ClusterField(name=label, type=_INT) for label in labels]


def _nested() -> list[ClusterField]:
    inner = LVType(kind=LVTypeKind.CLUSTER, fields=_cluster("Count", "count"))
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


def _terminal(index: int) -> Terminal:
    return Terminal(
        id=f"t{index}", index=index, direction="output", nmux_field_index=index
    )


def test_unbundle_and_bundle_by_name_use_the_distinct_names() -> None:
    fields = _nested()
    expressions = [_field_expr(_terminal(i), "data", fields) for i in range(4)]
    assert expressions == [
        "data.count",
        "data.count_2",
        "data.count_2.count",
        "data.count_2.count_2",
    ]
    assert [_field_name(_terminal(i), fields) for i in range(4)] == [
        "count",
        "count_2",
        "count",
        "count_2",
    ]


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

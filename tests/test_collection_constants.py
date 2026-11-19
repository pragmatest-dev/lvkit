"""A Set or Map constant is written as a Python set or dict."""

from __future__ import annotations

from lvkit.codegen.constant_value import constant_value_expression
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import make_ctx
from tests.prim_vi import scalar

STRING = scalar("String")
I32 = scalar("NumInt32")


def _collection(name: str) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type=name,
        element_type=STRING,
        value_type=I32 if name == "Map" else None,
    )


def test_an_empty_set_and_an_empty_map() -> None:
    ctx = make_ctx()
    assert constant_value_expression(ctx, _collection("Set"), []) == "set()"
    assert constant_value_expression(ctx, _collection("Map"), []) == "{}"


def test_a_set_with_elements() -> None:
    ctx = make_ctx()
    assert (
        constant_value_expression(ctx, _collection("Set"), ["a", "b"]) == "{'a', 'b'}"
    )


def test_a_map_with_entries() -> None:
    ctx = make_ctx()
    assert (
        constant_value_expression(ctx, _collection("Map"), [["a", "1"], ["b", "2"]])
        == "{'a': 1, 'b': 2}"
    )


def _cluster(typedef: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef,
        fields=[ClusterField(name="x", type=I32), ClusterField(name="n", type=STRING)],
    )


def test_a_map_constant_with_cluster_keys_has_the_tuple_a_wired_key_becomes() -> None:
    ctx = make_ctx()
    for typedef in ("Pt", None):
        lv_type = LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="Map",
            element_type=_cluster(typedef),
            value_type=I32,
        )
        entries = [[{"x": "1", "n": "a"}, "5"]]
        assert (
            constant_value_expression(ctx, lv_type, entries) == "{(1, 'a'): 5}"
        )


def test_a_set_constant_with_array_elements_is_a_set_of_tuples() -> None:
    ctx = make_ctx()
    lv_type = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Set",
        element_type=LVType(kind=LVTypeKind.ARRAY, element_type=I32, dimensions=1),
    )
    assert (
        constant_value_expression(ctx, lv_type, [["1", "2"], ["3"]])
        == "{(1, 2), (3,)}"
    )

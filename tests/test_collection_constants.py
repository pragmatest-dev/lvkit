"""A Set or Map constant is written as a Python set or dict."""

from __future__ import annotations

from lvkit.codegen.constant_value import constant_value_expression
from lvkit.models import LVType, LVTypeKind
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

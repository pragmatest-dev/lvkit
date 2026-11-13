"""The Map and Set functions run as Python on a dict and a set."""

from __future__ import annotations

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.prim_vi import Pin, generate, scalar

STRING = scalar("String")
I32 = scalar("NumInt32")
BOOL = scalar("Boolean")


def map_of(key: LVType, value: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Map",
        element_type=key,
        value_type=value,
    )


def set_of(element: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Set", element_type=element
    )


def _look(default_wired: bool = True, value: LVType = I32):
    controls = {"m": map_of(STRING, value), "key": STRING}
    pins = [
        Pin(11, "input", map_of(STRING, value), source="m"),
        Pin(10, "input", STRING, source="key"),
        Pin(9, "input", value, source="fallback" if default_wired else None),
        Pin(1, "output", value, sink="found"),
        Pin(2, "output", BOOL, sink="missing"),
    ]
    if default_wired:
        controls["fallback"] = value
    return generate(
        2458, "Look In Map", pins, controls, {"found": value, "missing": BOOL}
    )


def test_look_in_map_finds_a_key() -> None:
    source, fn = _look()
    assert "lvkit" not in source
    row = fn({"a": 1, "b": 2}, "b", -1)
    assert (row.found, row.missing) == (2, False)


def test_look_in_map_missing_key_gives_default_value() -> None:
    _, fn = _look()
    row = fn({"a": 1}, "z", 99)
    assert (row.found, row.missing) == (99, True)


def test_look_in_map_unwired_default_is_the_default_of_the_value_type() -> None:
    _, fn = _look(default_wired=False)
    assert fn({}, "z").found == 0
    _, fn = _look(default_wired=False, value=STRING)
    assert fn({}, "z").found == ""


def _insert(value_unchanged_wired: bool = False):
    pins = [
        Pin(11, "input", map_of(STRING, I32), source="m"),
        Pin(10, "input", STRING, source="key"),
        Pin(9, "input", I32, source="value"),
        Pin(3, "output", map_of(STRING, I32), sink="out"),
        Pin(2, "output", BOOL, sink="included"),
        Pin(1, "output", BOOL, sink="unchanged" if value_unchanged_wired else None),
    ]
    indicators = {"out": map_of(STRING, I32), "included": BOOL}
    if value_unchanged_wired:
        indicators["unchanged"] = BOOL
    return generate(
        2456,
        "Insert Into Map",
        pins,
        {"m": map_of(STRING, I32), "key": STRING, "value": I32},
        indicators,
    )


def test_insert_into_map_adds_and_replaces_without_changing_the_input() -> None:
    _, fn = _insert()
    original = {"a": 1}
    row = fn(original, "b", 2)
    assert (row.out, row.included) == ({"a": 1, "b": 2}, False)
    row = fn(original, "a", 5)
    assert (row.out, row.included) == ({"a": 5}, True)
    assert original == {"a": 1}


def test_insert_into_map_value_unchanged_is_a_gap() -> None:
    _, fn = _insert(value_unchanged_wired=True)
    with pytest.raises(NotImplementedError, match="value unchanged"):
        fn({}, "a", 1)


def test_a_cluster_key_is_a_gap() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="x", type=I32)],
    )
    _, fn = generate(
        2458,
        "Look In Map",
        [
            Pin(11, "input", map_of(cluster, I32), source="m"),
            Pin(10, "input", cluster, source="key"),
            Pin(1, "output", I32, sink="found"),
        ],
        {"m": map_of(cluster, I32), "key": cluster},
        {"found": I32},
    )
    with pytest.raises(NotImplementedError, match="key"):
        fn({}, None)


def _set_op(prim_id: int, name: str):
    return generate(
        prim_id,
        name,
        [
            Pin(11, "input", set_of(STRING), source="s"),
            Pin(10, "input", STRING, source="element"),
            Pin(3, "output", set_of(STRING), sink="out"),
            Pin(2, "output", BOOL, sink="flag"),
        ],
        {"s": set_of(STRING), "element": STRING},
        {"out": set_of(STRING), "flag": BOOL},
    )


def test_insert_into_set() -> None:
    _, fn = _set_op(2452, "Insert Into Set")
    original = {"a"}
    row = fn(original, "b")
    assert (row.out, row.flag) == ({"a", "b"}, False)
    row = fn(original, "a")
    assert (row.out, row.flag) == ({"a"}, True)
    assert original == {"a"}


def test_remove_from_set() -> None:
    _, fn = _set_op(2453, "Remove From Set")
    original = {"a", "b"}
    row = fn(original, "a")
    assert (row.out, row.flag) == ({"b"}, False)
    row = fn(original, "z")
    assert (row.out, row.flag) == ({"a", "b"}, True)
    assert original == {"a", "b"}


@pytest.mark.parametrize("collection", [map_of(STRING, I32), set_of(STRING)])
def test_collection_size_and_empty(collection: LVType) -> None:
    _, size = generate(
        2450,
        "Collection Size",
        [Pin(1, "input", collection, source="c"), Pin(0, "output", I32, sink="n")],
        {"c": collection},
        {"n": I32},
    )
    _, empty = generate(
        2451,
        "Empty Collection?",
        [Pin(1, "input", collection, source="c"), Pin(0, "output", BOOL, sink="e")],
        {"c": collection},
        {"e": BOOL},
    )
    sample = {"a": 1} if collection.underlying_type == "Map" else {"a"}
    assert size(sample).n == 1 and size(type(sample)()).n == 0
    assert empty(sample).e is False and empty(type(sample)()).e is True

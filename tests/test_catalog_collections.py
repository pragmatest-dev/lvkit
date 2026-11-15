"""The Map and Set functions run as Python on a dict and a set."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.prim_vi import Pin, array_of, generate, scalar

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


def _cluster(typedef: str | None = None, nested: bool = False) -> LVType:
    fields = [ClusterField(name="x", type=I32), ClusterField(name="Name", type=STRING)]
    if nested:
        inner = LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField("z", I32)])
        fields.append(ClusterField(name="inner", type=inner))
    return LVType(kind=LVTypeKind.CLUSTER, fields=fields, typedef_name=typedef)


def _map_fns(key: LVType):
    _, insert = generate(
        2456,
        "Insert Into Map",
        [
            Pin(11, "input", map_of(key, I32), source="m"),
            Pin(10, "input", key, source="key"),
            Pin(9, "input", I32, source="value"),
            Pin(3, "output", map_of(key, I32), sink="out"),
            Pin(2, "output", BOOL, sink="included"),
        ],
        {"m": map_of(key, I32), "key": key, "value": I32},
        {"out": map_of(key, I32), "included": BOOL},
    )
    _, look = generate(
        2458,
        "Look In Map",
        [
            Pin(11, "input", map_of(key, I32), source="m"),
            Pin(10, "input", key, source="key"),
            Pin(9, "input", I32, source="fallback"),
            Pin(1, "output", I32, sink="found"),
            Pin(2, "output", BOOL, sink="missing"),
        ],
        {"m": map_of(key, I32), "key": key, "fallback": I32},
        {"found": I32, "missing": BOOL},
    )
    return insert, look


def test_a_labelled_cluster_key_is_the_tuple_of_its_values() -> None:
    insert, look = _map_fns(_cluster("Pt"))
    made = insert({}, SimpleNamespace(x=1, name="a"), 7).out
    assert made == {(1, "a"): 7}
    again = look(made, SimpleNamespace(x=1, name="a"), -1)
    assert (again.found, again.missing) == (7, False)
    other = look(made, SimpleNamespace(x=2, name="a"), -1)
    assert (other.found, other.missing) == (-1, True)
    assert insert(made, SimpleNamespace(x=1, name="a"), 8).included is True


def test_an_unlabelled_cluster_key_is_the_tuple_of_its_values() -> None:
    insert, look = _map_fns(_cluster())
    made = insert({}, (1, "a"), 7).out
    assert made == {(1, "a"): 7}
    assert look(made, (1, "a"), -1).found == 7
    assert look(made, (1, "b"), -1).missing is True


def test_a_nested_cluster_key_is_a_nested_tuple() -> None:
    insert, look = _map_fns(_cluster("Pt", nested=True))
    key = SimpleNamespace(x=1, name="a", inner=(3,))
    made = insert({}, key, 7).out
    assert made == {(1, "a", (3,)): 7}
    assert look(made, key, -1).found == 7


def test_an_array_key_is_a_tuple() -> None:
    insert, look = _map_fns(array_of(I32))
    made = insert({}, [1, 2, 3], 7).out
    assert made == {(1, 2, 3): 7}
    assert look(made, [1, 2, 3], -1).found == 7
    assert look(made, [1, 2], -1).missing is True


def test_an_array_of_clusters_key_is_a_tuple_of_tuples() -> None:
    insert, look = _map_fns(array_of(_cluster()))
    made = insert({}, [(1, "a"), (2, "b")], 7).out
    assert made == {((1, "a"), (2, "b")): 7}
    assert look(made, [(1, "a"), (2, "b")], -1).found == 7


def test_a_queue_reference_is_a_key_by_identity() -> None:
    import queue

    reference = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="Queue"
    )
    insert, look = _map_fns(reference)
    first, other = queue.Queue(), queue.Queue()
    made = insert({}, first, 7).out
    assert look(made, first, -1).found == 7
    assert look(made, other, -1).missing is True


def test_the_value_stored_in_a_map_is_not_converted() -> None:
    cluster = _cluster("Pt")
    _, insert = generate(
        2456,
        "Insert Into Map",
        [
            Pin(11, "input", map_of(STRING, cluster), source="m"),
            Pin(10, "input", STRING, source="key"),
            Pin(9, "input", cluster, source="value"),
            Pin(3, "output", map_of(STRING, cluster), sink="out"),
        ],
        {"m": map_of(STRING, cluster), "key": STRING, "value": cluster},
        {"out": map_of(STRING, cluster)},
    )
    value = SimpleNamespace(x=1, name="a")
    assert insert({}, "k", value).out == {"k": value}


@pytest.mark.parametrize(
    "key, held",
    [
        (LVType(kind=LVTypeKind.CLASS, classname="Thing"), "a class object"),
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Map"), "a Map"),
        (LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Variant"), "a Variant"),
    ],
)
def test_a_key_that_cannot_be_hashed_is_a_gap_naming_it(
    key: LVType, held: str
) -> None:
    _, look = _map_fns(key)
    with pytest.raises(NotImplementedError, match=held):
        look({}, None, 0)


def test_a_cluster_holding_a_class_object_is_a_gap() -> None:
    thing = LVType(kind=LVTypeKind.CLASS, classname="Thing")
    key = LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField("o", thing)])
    _, look = _map_fns(key)
    with pytest.raises(NotImplementedError, match="a class object"):
        look({}, (None,), 0)


def test_a_set_of_clusters_inserts_removes_and_tests_membership() -> None:
    cluster = _cluster("Pt")
    kind = set_of(cluster)

    def op(prim_id: int, name: str):
        return generate(
            prim_id,
            name,
            [
                Pin(11, "input", kind, source="s"),
                Pin(10, "input", cluster, source="element"),
                Pin(3, "output", kind, sink="out"),
                Pin(2, "output", BOOL, sink="flag"),
            ],
            {"s": kind, "element": cluster},
            {"out": kind, "flag": BOOL},
        )[1]

    row = op(2452, "Insert Into Set")(set(), SimpleNamespace(x=1, name="a"))
    assert (row.out, row.flag) == ({(1, "a")}, False)
    row = op(2452, "Insert Into Set")(row.out, SimpleNamespace(x=1, name="a"))
    assert (row.out, row.flag) == ({(1, "a")}, True)
    row = op(2453, "Remove From Set")({(1, "a")}, SimpleNamespace(x=1, name="a"))
    assert (row.out, row.flag) == (set(), False)
    row = op(2453, "Remove From Set")({(1, "a")}, SimpleNamespace(x=9, name="a"))
    assert (row.out, row.flag) == ({(1, "a")}, True)


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

"""Flatten To String and Unflatten From String of a Map and of a Set: NI's
"Flattened Data" page gives a 32-bit count then each element (or key and value) in
key order. The 37-byte Map of the sample corpus, ``{'PinGroup1': ['Pin1', 'Pin2']}``,
is the worked example."""

from __future__ import annotations

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import Generated, array, generate, node, result_values, scalar

I32 = scalar("NumInt32")
STR = scalar("String")
PATH = scalar("Path")


def _set(element: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Set", element_type=element
    )


def _map(key: LVType, value: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Map",
        element_type=key,
        value_type=value,
    )


def round_trip(collection: LVType, gaps: list[Gap] | None = None) -> Generated:
    return generate(
        {"x": collection},
        {"data": STR, "back": collection},
        [
            node("ft", 1164, [(3, "output", STR), (11, "input", collection)]),
            node(
                "uf",
                1165,
                [
                    (2, "output", collection),
                    (7, "input", collection),
                    (11, "input", STR),
                ],
            ),
        ],
        [
            ("fp:x", "ft:11"),
            ("ft:3", "fp:data"),
            ("ft:3", "uf:11"),
            ("uf:2", "fp:back"),
        ],
        gaps=gaps,
    )


def test_the_corpus_map_flattens_as_its_bytes_and_reads_back() -> None:
    generated = round_trip(_map(STR, array(STR)))
    assert "lvkit" not in generated.source
    data, back = result_values(generated.function({"PinGroup1": ["Pin1", "Pin2"]}))
    assert data.encode("latin-1").hex() == (
        "00000001"
        "00000009" + b"PinGroup1".hex() + "00000002"
        "00000004" + b"Pin1".hex() + "00000004" + b"Pin2".hex()
    )
    assert back == {"PinGroup1": ["Pin1", "Pin2"]}


def test_a_map_is_written_in_key_order() -> None:
    generated = round_trip(_map(I32, STR))
    data, back = result_values(generated.function({3: "c", -1: "a", 2: "b"}))
    assert data.encode("latin-1").hex() == (
        "00000003"
        + "ffffffff"
        + "00000001"
        + b"a".hex()
        + "00000002"
        + "00000001"
        + b"b".hex()
        + "00000003"
        + "00000001"
        + b"c".hex()
    )
    assert back == {3: "c", -1: "a", 2: "b"}


def test_a_set_of_cluster_elements_is_written_in_order_and_read_as_tuples() -> None:
    record = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="s", type=STR), ClusterField(name="n", type=I32)],
    )
    generated = round_trip(_set(record))
    data, back = result_values(generated.function({("b", 1), ("a", 2)}))
    assert data.encode("latin-1").hex() == (
        "00000002"
        + "00000001"
        + b"a".hex()
        + "00000002"
        + "00000001"
        + b"b".hex()
        + "00000001"
    )
    assert back == {("a", 2), ("b", 1)}


def test_a_set_of_arrays_reads_as_tuples() -> None:
    generated = round_trip(_set(array("NumInt32")))
    _, back = result_values(generated.function({(1, 2), (3,)}))
    assert back == {(1, 2), (3,)}


def test_an_empty_set_is_a_zero_count() -> None:
    data, back = result_values(round_trip(_set(I32)).function(set()))
    assert data == "\x00\x00\x00\x00" and back == set()


def test_a_key_with_no_settled_order_stays_a_gap() -> None:
    gaps: list[Gap] = []
    round_trip(_map(PATH, I32), gaps)
    assert gaps and all("sort order is not settled" in g.reason for g in gaps)


@pytest.mark.parametrize("underlying", ["NumComplex128"])
def test_a_complex_key_stays_a_gap(underlying: str) -> None:
    gaps: list[Gap] = []
    round_trip(_set(scalar(underlying)), gaps)
    assert gaps and all("complex" in g.reason for g in gaps)

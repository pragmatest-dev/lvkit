"""Array To Cluster (1168): a 1D array to a cluster whose size is the node's."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, array_of, describe_and_lvnet, generate, scalar


def _cluster(element: LVType, size: int, typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name=typedef,
        fields=[ClusterField(name=f"[{i}]", type=element) for i in range(size)]
        if typedef is None
        else [ClusterField(name=f"Item {i}", type=element) for i in range(size)],
    )


def _vi(element: LVType, size: int, typedef: str | None = None, wired: bool = True):
    cluster = _cluster(element, size, typedef)
    return generate(
        1168,
        "Array To Cluster",
        [
            Pin(1, "input", array_of(element), source="items" if wired else None),
            Pin(0, "output", cluster, sink="cluster"),
        ],
        {"items": array_of(element)} if wired else {},
        {"cluster": cluster},
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=1168)
    assert entry is not None
    assert [(t.index, t.direction, t.name, t.type) for t in entry.terminals] == [
        (0, "output", "cluster", "Cluster"),
        (1, "input", "array", "Array"),
    ]


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(tmp_path, 1168, [(0, True), (1, False)])
    assert text.endswith("## Operations\nArray To Cluster [prim 1168]")
    assert "function Array_To_Cluster_10 : Array To Cluster" in lvnet


def test_array_of_the_cluster_size() -> None:
    source, fn = _vi(scalar("NumInt32"), 3)
    assert "lvkit" not in source
    assert fn([1, 2, 3]).cluster == (1, 2, 3)


def test_longer_array_is_truncated_to_the_cluster_size() -> None:
    _, fn = _vi(scalar("NumInt32"), 3)
    assert fn([1, 2, 3, 4, 5]).cluster == (1, 2, 3)


def test_shorter_array_is_filled_with_the_element_default() -> None:
    _, fn = _vi(scalar("NumInt32"), 4)
    assert fn([7]).cluster == (7, 0, 0, 0)
    _, fn = _vi(scalar("String"), 3)
    assert fn(["a"]).cluster == ("a", "", "")
    _, fn = _vi(scalar("Boolean"), 2)
    assert fn([]).cluster == (False, False)


def test_unwired_array_is_an_empty_array() -> None:
    _, fn = _vi(scalar("NumFloat64"), 2, wired=False)
    assert fn().cluster == (0.0, 0.0)


def test_cluster_size_nine_and_eleven_come_from_the_node() -> None:
    _, nine = _vi(scalar("String"), 9)
    _, eleven = _vi(scalar("String"), 11)
    assert len(nine(["x"] * 20).cluster) == 9
    assert len(eleven(["x"] * 20).cluster) == 11


def test_typedef_cluster_is_a_namespace_read_by_field() -> None:
    source, fn = _vi(scalar("NumInt32"), 3, typedef="Triple.ctl")
    assert "SimpleNamespace" in source
    result = fn([4, 5]).cluster
    assert (result.item_0, result.item_1, result.item_2) == (4, 5, 0)


def test_cluster_with_no_type_is_a_gap() -> None:
    _, fn = generate(
        1168,
        "Array To Cluster",
        [
            Pin(1, "input", array_of(scalar("NumInt32")), source="items"),
            Pin(0, "output", None, sink="cluster"),
        ],
        {"items": array_of(scalar("NumInt32"))},
        {"cluster": scalar("NumInt32")},
    )
    with pytest.raises(NotImplementedError, match="no cluster type"):
        fn([1])

"""Cluster To Array (1169): the fields of a cluster, in order, as a 1D array."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, array_of, describe_and_lvnet, generate, scalar


def _cluster(element: LVType, size: int, typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name=typedef,
        fields=[ClusterField(name=f"Item {i}", type=element) for i in range(size)],
    )


def _vi(element: LVType, size: int, typedef: str | None = None, wired: bool = True):
    cluster = _cluster(element, size, typedef)
    return generate(
        1169,
        "Cluster To Array",
        [
            Pin(1, "input", cluster, source="cluster" if wired else None),
            Pin(0, "output", array_of(element), sink="items"),
        ],
        {"cluster": cluster} if wired else {},
        {"items": array_of(element)},
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=1169)
    assert entry is not None
    assert [(t.index, t.direction, t.name, t.type) for t in entry.terminals] == [
        (0, "output", "array", "Array"),
        (1, "input", "cluster", "Cluster"),
    ]


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(tmp_path, 1169, [(0, True), (1, False)])
    assert text.endswith("## Operations\nCluster To Array [prim 1169]")
    assert "function Cluster_To_Array_10 : Cluster To Array" in lvnet


def test_anonymous_cluster_is_its_fields_in_order() -> None:
    source, fn = _vi(scalar("NumInt32"), 3)
    assert "lvkit" not in source
    assert fn((7, 8, 9)).items == [7, 8, 9]


def test_cluster_of_strings() -> None:
    _, fn = _vi(scalar("String"), 2)
    assert fn(("a", "")).items == ["a", ""]


def test_typedef_cluster_is_read_by_field_name() -> None:
    source, fn = _vi(scalar("NumInt32"), 3, typedef="Triple.ctl")
    assert "SimpleNamespace" not in source
    assert fn(SimpleNamespace(item_0=1, item_1=2, item_2=3)).items == [1, 2, 3]


def test_unwired_cluster_is_the_default_of_its_type() -> None:
    _, fn = _vi(scalar("NumFloat64"), 2, wired=False)
    assert fn().items == [0.0, 0.0]


def test_cluster_with_no_type_is_a_gap() -> None:
    _, fn = generate(
        1169,
        "Cluster To Array",
        [
            Pin(1, "input", None, source="cluster"),
            Pin(0, "output", array_of(scalar("NumInt32")), sink="items"),
        ],
        {"cluster": scalar("NumInt32")},
        {"items": array_of(scalar("NumInt32"))},
    )
    with pytest.raises(NotImplementedError, match="no cluster type"):
        fn(1)

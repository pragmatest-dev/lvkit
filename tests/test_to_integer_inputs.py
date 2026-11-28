"""The To <integer> conversions on every input NI's pages list (a number, an array
or cluster of numbers, an array of clusters), plus an enum (NI, "Numeric
Conversion": "LabVIEW treats enums as unsigned integers"), run on the generated
code; an input they do not list stays a gap."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.prim_vi import Pin, array_of, generate, scalar

INF = math.inf


def _convert(prim_id: int, name: str, in_type, out_type, gaps=None):
    return generate(
        prim_id,
        name,
        [
            Pin(1, "input", in_type, source="n"),
            Pin(0, "output", out_type, sink="r"),
        ],
        {"n": in_type},
        {"r": out_type},
        gaps=gaps,
    )


def _enum(width: str = "UnitUInt8") -> LVType:
    return LVType(kind=LVTypeKind.ENUM, underlying_type=width)


def _cluster(*members: tuple[str, LVType], typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name=typedef,
        fields=[ClusterField(name=n, type=t) for n, t in members],
    )


_WIDTHS = [
    (1140, "To Byte Integer", "NumInt8", -128, 127),
    (1141, "To Word Integer", "NumInt16", -(2**15), 2**15 - 1),
    (1142, "To Long Integer", "NumInt32", -(2**31), 2**31 - 1),
    (1155, "To Quad Integer", "NumInt64", -(2**63), 2**63 - 1),
    (1143, "To Unsigned Byte Integer", "NumUInt8", 0, 255),
    (1144, "To Unsigned Word Integer", "NumUInt16", 0, 2**16 - 1),
    (1145, "To Unsigned Long Integer", "NumUInt32", 0, 2**32 - 1),
    (1156, "To Unsigned Quad Integer", "NumUInt64", 0, 2**64 - 1),
]


@pytest.mark.parametrize("prim_id,name,out,low,high", _WIDTHS)
def test_an_enum_is_an_unsigned_integer_that_keeps_its_low_bits(
    prim_id: int, name: str, out: str, low: int, high: int
) -> None:
    _, convert = _convert(prim_id, name, _enum(), scalar(out))
    assert convert(5).r == 5
    assert convert(255).r == (255 if high >= 255 else (-1 if low < 0 else 255))


def test_an_enum_wider_than_the_output_keeps_the_low_bits() -> None:
    _, convert = _convert(
        1141, "To Word Integer", _enum("UnitUInt32"), scalar("NumInt16")
    )
    assert convert(65537).r == 1
    assert convert(40000).r == 40000 - 65536


def test_a_ring_array_converts_each_element() -> None:
    ring = LVType(kind=LVTypeKind.RING, underlying_type="UnitUInt16")
    _, convert = _convert(
        1142, "To Long Integer", array_of(ring), array_of(scalar("NumInt32"))
    )
    assert convert([1, 2, 3]).r == [1, 2, 3]


def test_an_anonymous_cluster_converts_each_member_by_its_type() -> None:
    cluster = _cluster(("a", scalar("NumFloat64")), ("b", scalar("NumInt32")))
    result = _cluster(("a", scalar("NumInt16")), ("b", scalar("NumInt16")))
    _, convert = _convert(1141, "To Word Integer", cluster, result)
    assert convert((INF, 70000)).r == (2**15 - 1, 70000 - 65536)
    assert convert((-2.5, -5)).r == (-2, -5)


def test_a_typedef_cluster_gives_a_typedef_cluster() -> None:
    cluster = _cluster(
        ("Left", scalar("NumFloat64")),
        ("Right", scalar("NumFloat64")),
        typedef="lib:Point.ctl",
    )
    result = _cluster(
        ("Left", scalar("NumInt32")),
        ("Right", scalar("NumInt32")),
        typedef="lib:Point.ctl",
    )
    _, convert = _convert(1142, "To Long Integer", cluster, result)
    got = convert(SimpleNamespace(left=1.5, right=-INF)).r
    assert (got.left, got.right) == (2, -(2**31))


def test_a_nested_cluster_and_an_array_member_convert_all_the_way_down() -> None:
    inner = _cluster(("x", scalar("NumFloat64")))
    cluster = _cluster(("in", inner), ("list", array_of(scalar("NumFloat64"))))
    inner_out = _cluster(("x", scalar("NumInt32")))
    result = _cluster(("in", inner_out), ("list", array_of(scalar("NumInt32"))))
    _, convert = _convert(1142, "To Long Integer", cluster, result)
    assert convert(((2.5,), [1.5, INF])).r == ((2,), [2, 2**31 - 1])


def test_an_array_of_clusters_converts_each_cluster() -> None:
    cluster = _cluster(("a", scalar("NumFloat64")), ("b", scalar("NumFloat64")))
    result = _cluster(("a", scalar("NumUInt8")), ("b", scalar("NumUInt8")))
    _, convert = _convert(
        1143, "To Unsigned Byte Integer", array_of(cluster), array_of(result)
    )
    assert convert([(1.5, -3.0), (300.0, 2.5)]).r == [(2, 0), (255, 2)]


def test_a_string_input_stays_a_gap_naming_what_it_is() -> None:
    gaps: list[Gap] = []
    _, convert = _convert(
        1142, "To Long Integer", scalar("String"), scalar("NumInt32"), gaps
    )
    assert len(gaps) == 1
    assert "To Long Integer" in gaps[0].message
    assert "array or cluster of numbers" in gaps[0].message
    with pytest.raises(NotImplementedError):
        convert("x")


def test_a_cluster_with_a_string_member_stays_a_gap() -> None:
    gaps: list[Gap] = []
    cluster = _cluster(("a", scalar("NumInt32")), ("s", scalar("String")))
    result = _cluster(("a", scalar("NumInt32")), ("s", scalar("NumInt32")))
    _convert(1142, "To Long Integer", cluster, result, gaps)
    assert len(gaps) == 1

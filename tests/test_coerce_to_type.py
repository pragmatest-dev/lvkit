"""Coerce To Type (2402): the input's value as the type of ``type``."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, array_of, describe_and_lvnet, generate, scalar


def _vi(source: LVType | None, target: LVType | None, *, x_wired: bool = True):
    controls = {"x": source, "kind": target} if x_wired else {"kind": target}
    return generate(
        2402,
        "Coerce To Type",
        [
            Pin(0, "input", source, source="x" if x_wired else None),
            Pin(1, "input", target, source="kind"),
            Pin(2, "output", target, sink="coerced"),
        ],
        {k: v for k, v in controls.items() if v is not None},
        {"coerced": target or scalar("Void")},
    )


def _cluster(typedef: str | None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name=typedef,
        fields=[ClusterField(name="a", type=scalar("NumInt32"))],
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=2402)
    assert entry is not None
    assert [(t.index, t.direction, t.name) for t in entry.terminals] == [
        (0, "input", "x"),
        (1, "input", "type"),
        (2, "output", "coerced x"),
    ]


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(
        tmp_path, 2402, [(0, False), (1, False), (2, True)]
    )
    assert text.endswith("## Operations\nCoerce To Type [prim 2402]")
    assert "function Coerce_To_Type_10 : Coerce To Type" in lvnet


def test_the_same_type_is_the_value_itself() -> None:
    """A rename of a refnum, a number, an enum or an array."""
    for kind in (
        scalar("Refnum"),
        scalar("NumInt64"),
        LVType(kind=LVTypeKind.ENUM, underlying_type="UnitUInt16"),
        array_of(scalar("String")),
    ):
        source, fn = _vi(kind, kind)
        assert "lvkit" not in source
        assert "coerced" not in source.split("def ")[-1].split("return")[0]
        assert fn("v", None).coerced == "v"


def test_a_float_goes_to_the_nearest_integer_and_saturates() -> None:
    _, fn = _vi(scalar("NumFloat64"), scalar("NumInt8"))
    assert [fn(x, 0).coerced for x in (2.5, 3.5, -2.5, 1.4, 300.0, -300.0)] == [
        2,
        4,
        -2,
        1,
        127,
        -128,
    ]
    assert fn(float("inf"), 0).coerced == 127
    _, unsigned = _vi(scalar("NumFloat64"), scalar("NumUInt16"))
    assert [unsigned(x, 0).coerced for x in (-5.0, 70000.0, 7.5)] == [0, 65535, 8]


def test_an_integer_to_a_smaller_integer_keeps_the_low_bits() -> None:
    source, fn = _vi(scalar("NumInt32"), scalar("NumUInt8"))
    assert "ctypes.c_uint8(" in source
    assert [fn(x, 0).coerced for x in (300, 2147483647, -1, 7)] == [44, 255, 255, 7]
    _, signed = _vi(scalar("NumUInt32"), scalar("NumInt16"))
    assert signed(40000, 0).coerced == -25536


def test_an_integer_to_a_wider_integer_is_the_value() -> None:
    source, fn = _vi(scalar("NumUInt8"), scalar("NumInt32"))
    assert "ctypes" not in source
    assert fn(200, 0).coerced == 200


def test_a_number_to_floating_point_or_complex() -> None:
    _, to_float = _vi(scalar("NumInt16"), scalar("NumFloat64"))
    assert to_float(-3, 0.0).coerced == -3.0
    assert isinstance(to_float(-3, 0.0).coerced, float)
    _, to_complex = _vi(scalar("NumFloat64"), scalar("NumComplex128"))
    assert to_complex(1.5, 0j).coerced == 1.5 + 0j


def test_unwired_x_is_the_default_of_the_type() -> None:
    _, fn = _vi(scalar("NumInt32"), scalar("NumUInt8"), x_wired=False)
    assert fn(0).coerced == 0


def test_a_cluster_with_and_without_a_typedef_is_a_gap() -> None:
    _, fn = _vi(_cluster(None), _cluster("Point.ctl"))
    with pytest.raises(NotImplementedError, match="has no code generation yet"):
        fn((1,), None)


def test_a_type_that_did_not_resolve_is_a_gap() -> None:
    _, fn = _vi(None, scalar("NumInt32"), x_wired=False)
    with pytest.raises(NotImplementedError, match="no type on x"):
        fn(0)

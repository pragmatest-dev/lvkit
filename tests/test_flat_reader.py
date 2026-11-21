"""The reader of flattened data that a generated package's ``variants`` module
holds reads, by a type known only from its type string, what Flatten To String
writes for the same type and value when the code is generated (the layouts of
``codegen/flat.py``): every type it reads round-trips in both byte orders, and a
type it does not read raises naming it."""

from __future__ import annotations

import struct
from datetime import UTC

import hightime
import pytest

from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from lvkit.type_string import encode
from tests.helpers import generated_variants
from tests.native_vi import Generated, array, generate, node, result_values, scalar

STR = scalar("String")
BOOL = scalar("Boolean")
I16 = scalar("NumInt16")
I32 = scalar("NumInt32")
U8 = scalar("NumUInt8")
F32 = scalar("NumFloat32")
DBL = scalar("NumFloat64")
U16 = scalar("NumUInt16")


def cluster(*members: LVType) -> LVType:
    return LVType(
        LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(f"f{i}", m, labelled=False) for i, m in enumerate(members)
        ],
    )


def enum(*labels: str) -> LVType:
    return LVType(
        LVTypeKind.ENUM,
        underlying_type="UnitUInt8",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
    )


def flatten(source: LVType, order: int, prefix: bool) -> Generated:
    """A VI whose Flatten To String flattens its ``x`` control, with the byte
    order and the prepend-size option written as constants."""
    return generate(
        {"x": source},
        {"r": STR},
        [
            node(
                "ft",
                1164,
                [
                    (3, "output", STR),
                    (11, "input", source),
                    (9, "input", U16),
                    (10, "input", BOOL),
                ],
            )
        ],
        [("fp:x", "ft:11"), ("c9", "ft:9"), ("c10", "ft:10"), ("ft:3", "fp:r")],
        constants={"c9": (str(order), U16), "c10": (str(prefix), BOOL)},
    )


def round_trip(lv_type: LVType, value, order: int = 0, prefix: bool = True):
    """``value`` flattened when generating, then read by the type string."""
    v = generated_variants()
    flat = result_values(flatten(lv_type, order, prefix).function(value))[0]
    runtime = v.type_from_string(encode(lv_type))
    prefix_char = {0: ">", 1: "=", 2: "<"}[order]
    return v.unflatten_data(flat.encode("latin-1"), runtime, prefix_char, prefix)


CASES = [
    (U8, 200),
    (I16, -5),
    (I32, -123456),
    (F32, 1.5),
    (DBL, -2.25),
    (BOOL, True),
    (BOOL, False),
    (STR, "ABC"),
    (STR, ""),
    (array("NumInt16"), [1, -2, 3]),
    (array("NumInt16"), []),
    (array("String"), ["a", "bcd"]),
    (array("NumInt8", 2), [[1, 2, 3], [4, 5, 6]]),
    (array("Boolean", 2), [[True], [False]]),
    (cluster(STR, I16), ("ABC", 4)),
    (cluster(I32, cluster(STR, DBL), array("NumUInt8")), (7, ("x", 0.5), [1, 2])),
    (enum("a", "b", "c"), 2),
    (scalar("NumFloatExt"), 1.5),
    (scalar("NumComplex128"), 1 + 2j),
    (scalar("NumComplex64"), 0.5 - 1j),
]


@pytest.mark.parametrize("order", [0, 2])
@pytest.mark.parametrize(("lv_type", "value"), CASES)
def test_what_generation_flattens_is_read_back(lv_type, value, order) -> None:
    assert round_trip(lv_type, value, order) == value


def test_a_string_and_an_array_without_sizes() -> None:
    assert round_trip(STR, "ABC", prefix=False) == "ABC"
    assert round_trip(array("NumInt16"), [1, 2, 3], prefix=False) == [1, 2, 3]


def test_nis_flattened_data_page_example_reads() -> None:
    v = generated_variants()
    data = bytes.fromhex("0000000200000003010203040506")
    lv_type = v.type_from_string(encode(array("NumInt8", 2)))
    assert v.unflatten_data(data, lv_type) == [[1, 2, 3], [4, 5, 6]]


def test_a_type_with_no_layout_raises_naming_it() -> None:
    v = generated_variants()
    refnum = v.LVType(0x70, "Refnum", None)
    with pytest.raises(NotImplementedError, match="'Refnum'.*code 112"):
        v.unflatten_data(b"\x00" * 4, refnum)
    waveform = v.type_from_string([4, 0x54])
    with pytest.raises(NotImplementedError, match="not read"):
        v.unflatten_data(b"\x00" * 8, waveform)


TIMESTAMP = LVType(
    LVTypeKind.PRIMITIVE, underlying_type="MeasureData", measure_flavor="TimeStamp"
)
_EPOCH = hightime.datetime(1904, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("order", [0, 1, 2])
@pytest.mark.parametrize(
    "value",
    [
        _EPOCH,
        hightime.datetime(2024, 5, 17, 13, 45, 6, 250000, tzinfo=UTC),
        hightime.datetime(1850, 1, 1, tzinfo=UTC),
    ],
)
def test_a_time_stamp_flattened_when_generating_is_read_back(value, order) -> None:
    assert round_trip(TIMESTAMP, value, order) == value


def test_a_time_stamp_is_sixteen_big_endian_bytes() -> None:
    v = generated_variants()
    runtime = v.type_from_string(encode(TIMESTAMP))
    assert runtime.name == "MeasureData"
    data = struct.pack(">qQ", 3_000_000_000, 2**63)
    value = v.unflatten_data(data, runtime)
    assert value == _EPOCH + hightime.timedelta(seconds=3_000_000_000.5)
    variant = v.Variant(value, runtime)
    numbers, flat = v.variant_to_flattened(variant)
    assert numbers == encode(TIMESTAMP)
    assert flat.encode("latin-1") == data
    back = v.variant_from_flattened(flat, numbers)
    assert back.value == value
    assert back.type == runtime


def test_a_variant_of_the_time_stamp_default_flattens_as_zero_bytes() -> None:
    v = generated_variants()
    runtime = v.type_from_string(encode(TIMESTAMP))
    _, flat = v.variant_to_flattened(v.Variant(None, runtime))
    assert flat == "\x00" * 16


def test_a_time_stamp_in_a_cluster_follows_its_neighbours() -> None:
    value = (7, hightime.datetime(2000, 1, 1, tzinfo=UTC))
    assert round_trip(cluster(I32, TIMESTAMP), value) == value


def test_a_variant_named_by_its_label_flattens_a_time_stamp_with_the_name() -> None:
    v = generated_variants()
    runtime = v.type_from_string(encode(TIMESTAMP))
    named = v.Variant(_EPOCH, runtime, {}, "when")
    numbers, _ = v.variant_to_flattened(named)
    assert numbers == encode(TIMESTAMP, "when")
    assert v.read_type(v.LVType(0x54, "MeasureData", tuple(numbers))).name == "when"


def test_a_variant_read_from_a_named_type_string_equals_the_unnamed_one() -> None:
    """The name that ends a type string is the variant's name, which two equal
    variants need not share, and the label a type is written with for messages
    is not part of what it is."""
    v = generated_variants()
    unnamed = encode(cluster(STR))
    named = encode(cluster(STR), "gangster")
    data = "\x00\x00\x00\x03abc"
    read = v.variant_from_flattened(data, named)
    plain = v.variant_from_flattened(data, unnamed)
    assert read.name == "gangster"
    assert read.type.type_string == tuple(unnamed)
    assert read == plain
    labelled = v.LVType(read.type.code, "cluster{f0}", read.type.type_string)
    assert read == v.Variant(("abc",), labelled, {}, "other")
    assert v.variant_to_flattened(read)[0] == named


def test_truncated_data_is_reported() -> None:
    v = generated_variants()
    lv_type = v.type_from_string(encode(STR))
    with pytest.raises(Exception, match="runs past the end"):
        v.unflatten_data(b"\x00\x00\x00\x09AB", lv_type)

"""LabVIEW's flattened-data format as generated struct code: NI's own examples
from the "Flattened Data" page, Type Cast, Swap Bytes / Swap Words, and the type
layouts read from wire types. Each case is generated on a one-node diagram and
run."""

from __future__ import annotations

import pytest

from lvkit.codegen.flat import Integer, Real, Record, Sequence, Text, layout_of
from lvkit.conversion_gap import ConversionGap
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import array, generate, node, scalar

STR = scalar("String")
BOOL = scalar("Boolean")
I16 = scalar("NumInt16")
I32 = scalar("NumInt32")
U8 = scalar("NumUInt8")
U16 = scalar("NumUInt16")
U32 = scalar("NumUInt32")
F32 = scalar("NumFloat32")
DBL = scalar("NumFloat64")


def cluster(*fields: tuple[str, LVType], named: bool = False) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Record" if named else None,
        fields=[ClusterField(name=name, type=type_) for name, type_ in fields],
    )


def cast(source: LVType, target: LVType):
    return generate(
        {"x": source},
        {"r": target},
        [
            node(
                "tc",
                1166,
                [(0, "input", source), (1, "input", target), (2, "output", target)],
            )
        ],
        [("fp:x", "tc:0"), ("tc:2", "fp:r")],
    ).function


def flattened_hex(value, source: LVType) -> str:
    return cast(source, STR)(value).r.encode("latin-1").hex()


def swap(data: LVType, prim_id: int):
    return generate(
        {"x": data},
        {"r": data},
        [node("sw", prim_id, [(0, "output", data), (1, "input", data)])],
        [("fp:x", "sw:1"), ("sw:0", "fp:r")],
    ).function


def text(data: bytes) -> str:
    return data.decode("latin-1")


def test_numbers_flatten_big_endian() -> None:
    # "a 32-bit integer with a value of -19 is flattened to FFFF FFED"
    assert flattened_hex(-19, I32) == "ffffffed"
    # "a double-precision floating-point number with a value equal to 1/4 is
    # flattened to 3FD0 0000 0000 0000"
    assert flattened_hex(0.25, DBL) == "3fd0000000000000"
    # "A Boolean value of TRUE ... FALSE is 00"; "the preferred value for TRUE is 01"
    assert flattened_hex(True, BOOL) == "01"
    assert flattened_hex(False, BOOL) == "00"


def test_string_has_a_length_prefix_inside_a_cluster() -> None:
    # "A flattened cluster of a string ABC and a 16-bit integer of value 4 is
    # 0000 0003 4142 4300 04"
    record = cluster(("name", STR), ("count", I16))
    assert flattened_hex(("ABC", 4), record) == "000000034142430004"
    back = cast(STR, record)
    assert back(text(bytes.fromhex("000000034142430004"))).r == ("ABC", 4)


def test_cluster_is_the_concatenation_of_its_elements() -> None:
    # "a 16-bit integer of value 4 and a 32-bit integer of value 12 is 0004 0000 000C"
    assert flattened_hex((4, 12), cluster(("a", I16), ("b", I32))) == "00040000000c"
    # "a 16-bit integer of value 7, a cluster of a 16-bit integer of value 8, and a
    # 16-bit integer of value 9 is 0007 0008 0009"
    nested = cluster(("a", I16), ("inner", cluster(("b", I16))), ("c", I16))
    assert flattened_hex((7, (8,), 9), nested) == "000700080009"
    back = cast(STR, nested)
    assert back(text(bytes.fromhex("000700080009"))).r == (7, (8,), 9)


def test_array_has_a_size_per_dimension_before_its_data() -> None:
    # "{ {1, 2, 3}, {4, 5, 6} } is flattened to 0000 0002 0000 0003 0102 0304 0506"
    grid = array("NumUInt8", 2)
    data = [[1, 2, 3], [4, 5, 6]]
    assert flattened_hex(data, grid) == "00000002000000030102030405" + "06"
    assert cast(STR, grid)(cast(grid, STR)(data).r).r == data
    # "{T, F, T, T} is flattened to 0000 0004 0100 0101": an array inside a
    # cluster carries its size.
    flags = cluster(("flags", array("Boolean")))
    assert flattened_hex(([True, False, True, True],), flags) == "0000000401000101"


def test_named_cluster_comes_back_as_a_namespace() -> None:
    record = cluster(("x", I16), ("label", STR), named=True)
    value = cast(STR, record)(text(b"\xff\xfe\x00\x00\x00\x02ab")).r
    assert (value.x, value.label) == (-2, "ab")
    assert flattened_hex(value, record) == "fffe000000026162"


def test_type_cast_between_a_byte_array_and_a_cluster() -> None:
    record = cluster(("a", I32), ("b", U16), ("c", F32))
    data = [0, 0, 1, 0, 0, 7, 0x3F, 0x80, 0, 0]
    assert cast(array("NumUInt8"), record)(data).r == (256, 7, 1.0)
    assert cast(record, array("NumUInt8"))((256, 7, 1.0)).r == data


def test_type_cast_pads_and_truncates_as_ni_describes() -> None:
    # "an 8-bit unsigned integer with value 1 type cast to a 16-bit unsigned
    # integer results in a value of 256"
    assert cast(U8, U16)(1).r == 256
    # a larger source "uses the upper bytes of x and discards the remaining lower
    # bytes"
    assert cast(U32, U16)(0x12345678).r == 0x1234
    # "if you typecast an array of four 16-bit integers to an array of 32-bit
    # integers, the output array contains two elements"
    assert cast(array("NumInt16"), array("NumInt32"))([1, 2, 3, 4]).r == [
        0x10002,
        0x30004,
    ]


def test_type_cast_refnum_flattens_as_a_32_bit_integer() -> None:
    refnum = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="ByteStream"
    )
    assert cast(U32, refnum)(4660).r == 4660
    assert cast(refnum, U32)(4660).r == 4660


def test_types_with_no_flattened_form_stop_generation() -> None:
    with pytest.raises(ConversionGap, match="does not handle"):
        cast(U32, scalar("Variant"))
    with pytest.raises(ConversionGap, match="does not handle"):
        cast(I32, array("Variant"))


def test_swap_bytes_and_words_match_nis_examples() -> None:
    # "swaps 1234 with 5678 and 90AB with CDEF, returning 56781234CDEF90AB"
    assert swap(scalar("NumUInt64"), 1163)(0x1234567890ABCDEF).r == 0x56781234CDEF90AB
    # swap bytes swaps the 8 bits of every word: a 16-bit integer, and each 16-bit
    # word of a 32-bit one
    assert swap(U16, 1162)(0x1234).r == 0x3412
    assert swap(U32, 1162)(0x12345678).r == 0x34127856
    # a signed integer keeps its sign convention
    assert swap(I16, 1162)(-2).r == -257
    # an 8-bit integer, and the words of a 16-bit one, are left alone
    assert swap(U8, 1162)(0x12).r == 0x12
    assert swap(U16, 1163)(0x1234).r == 0x1234


def test_swap_acts_only_on_the_integers_of_a_cluster_and_each_array_element() -> None:
    record = cluster(
        ("n", U16), ("x", F32), ("list", array("NumUInt16")), ("label", STR)
    )
    assert swap(record, 1162)((0x0102, 1.5, [0x0304, 0x0506], "ab")).r == (
        0x0201,
        1.5,
        [0x0403, 0x0605],
        "ab",
    )


def test_layouts_of_wire_types() -> None:
    assert layout_of(array("NumUInt8")) == Sequence(Integer("B", "c_uint8", 1), 1)
    assert layout_of(array("NumFloat32", 2)) == Sequence(Real("f", 4), 2)
    layout = layout_of(
        cluster(("data size", I32), ("ok", BOOL), ("level", DBL), ("text", STR))
    )
    assert isinstance(layout, Record) and not layout.named
    assert [field.name for field in layout.fields] == [
        "data_size",
        "ok",
        "level",
        "text",
    ]
    assert layout.fields[3].layout == Text()
    byte_stream = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="ByteStream"
    )
    visa = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="VisaRef"
    )
    assert layout_of(byte_stream) == Integer("I", "c_uint32", 4)
    assert layout_of(visa) == Text()  # flattened as a string


def test_integers_wrap_around_at_their_width() -> None:
    # generated arithmetic does not wrap, LabVIEW integers do
    assert swap(U32, 1162)(0x1_1234_5678).r == 0x34127856
    assert cast(U32, U32)(0x1_0000_0001).r == 0x1_0000_0001  # same type: kept
    assert cast(U32, STR)(0x1_0000_0001).r == "\x00\x00\x00\x01"

"""LabVIEW's flattened-data format (runtime/flat.py): NI's own examples from the
"Flattened Data" page, Type Cast, and Swap Bytes / Swap Words."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lvkit.runtime import flat, lv


def _hex(data: bytes) -> str:
    return data.hex()


def test_numbers_flatten_big_endian() -> None:
    # "a 32-bit integer with a value of -19 is flattened to FFFF FFED"
    assert _hex(flat.flatten(-19, "i32")) == "ffffffed"
    # "a double-precision floating-point number with a value equal to 1/4 is
    # flattened to 3FD0 0000 0000 0000"
    assert _hex(flat.flatten(0.25, "f64")) == "3fd0000000000000"
    # "A Boolean value of TRUE ... FALSE is 00"; "the preferred value for TRUE is 01"
    assert _hex(flat.flatten(True, "bool")) == "01"
    assert _hex(flat.flatten(False, "bool")) == "00"


def test_string_has_a_length_prefix_inside_a_cluster() -> None:
    # "a string type with value ABC is flattened to 0000 0003 4142 43"
    assert _hex(flat.flatten("ABC", "str", prefix=True)) == "00000003414243"
    # "A flattened cluster of a string ABC and a 16-bit integer of value 4 is
    # 0000 0003 4142 4300 04"
    spec = ("cluster", False, (("name", "str"), ("count", "i16")))
    assert _hex(flat.flatten(("ABC", 4), spec)) == "000000034142430004"
    assert flat.unflatten(bytes.fromhex("000000034142430004"), spec) == ("ABC", 4)


def test_cluster_is_the_concatenation_of_its_elements() -> None:
    # "a 16-bit integer of value 4 and a 32-bit integer of value 12 is 0004 0000 000C"
    flat_pair = ("cluster", False, (("a", "i16"), ("b", "i32")))
    assert _hex(flat.flatten((4, 12), flat_pair)) == "00040000000c"
    # "a 16-bit integer of value 7, a cluster of a 16-bit integer of value 8, and a
    # 16-bit integer of value 9 is 0007 0008 0009"
    nested = (
        "cluster",
        False,
        (
            ("a", "i16"),
            ("inner", ("cluster", False, (("b", "i16"),))),
            ("c", "i16"),
        ),
    )
    assert _hex(flat.flatten((7, (8,), 9), nested)) == "000700080009"
    assert flat.unflatten(bytes.fromhex("000700080009"), nested) == (7, (8,), 9)


def test_array_has_a_size_per_dimension_before_its_data() -> None:
    # "{ {1, 2, 3}, {4, 5, 6} } is flattened to 0000 0002 0000 0003 0102 0304 0506"
    spec = ("array", "u8", 2)
    data = [[1, 2, 3], [4, 5, 6]]
    assert _hex(flat.flatten(data, spec)) == "00000002000000030102030405" + "06"
    assert flat.unflatten(flat.flatten(data, spec), spec) == data
    # "{T, F, T, T} is flattened to 0000 0004 0100 0101"
    booleans = ("array", "bool", 1)
    assert _hex(flat.flatten([True, False, True, True], booleans, prefix=True)) == (
        "0000000401000101"
    )


def test_named_cluster_comes_back_as_a_namespace() -> None:
    spec = ("cluster", True, (("x", "i16"), ("label", "str")))
    value = flat.unflatten(flat.flatten(SimpleNamespace(x=-2, label="ab"), spec), spec)
    assert (value.x, value.label) == (-2, "ab")


def test_cluster_given_as_a_dictionary_flattens_by_position() -> None:
    spec = ("cluster", False, (("vertical", "i16"), ("horizontal", "i16")))
    assert flat.flatten({"vertical": 1, "horizontal": 2}, spec) == b"\0\x01\0\x02"


def test_type_cast_between_a_byte_array_and_a_cluster() -> None:
    spec = ("cluster", False, (("a", "i32"), ("b", "u16"), ("c", "f32")))
    data = [0, 0, 1, 0, 0, 7, 0x3F, 0x80, 0, 0]
    assert lv.type_cast(data, "u8[]", spec) == (256, 7, 1.0)
    assert lv.type_cast((256, 7, 1.0), spec, "u8[]") == data


def test_type_cast_pads_and_truncates_as_ni_describes() -> None:
    # "an 8-bit unsigned integer with value 1 type cast to a 16-bit unsigned
    # integer results in a value of 256"
    assert lv.type_cast(1, "u8", "u16") == 256
    # a larger source "uses the upper bytes of x and discards the remaining lower
    # bytes"
    assert lv.type_cast(0x12345678, "u32", "u16") == 0x1234
    # "if you typecast an array of four 16-bit integers to an array of 32-bit
    # integers, the output array contains two elements"
    assert lv.type_cast([1, 2, 3, 4], "i16[]", "i32[]") == [0x10002, 0x30004]


def test_type_cast_refnum_flattens_as_a_32_bit_integer() -> None:
    assert lv.type_cast(4660, "u32", "refnum") == 4660
    assert lv.type_cast(4660, "refnum", "u32") == 4660


def test_unsupported_types_raise() -> None:
    with pytest.raises(NotImplementedError):
        lv.type_cast(1, "u32", "extended")
    with pytest.raises(NotImplementedError):
        lv.type_cast(1, "i32", ("array", "extended", 1))


def test_swap_bytes_and_words_match_nis_examples() -> None:
    # "swaps 1234 with 5678 and 90AB with CDEF, returning 56781234CDEF90AB"
    assert lv.swap_words(0x1234567890ABCDEF, "u64") == 0x56781234CDEF90AB
    # swap bytes swaps the 8 bits of every word: a 16-bit integer, and each 16-bit
    # word of a 32-bit one
    assert lv.swap_bytes(0x1234, "u16") == 0x3412
    assert lv.swap_bytes(0x12345678, "u32") == 0x34127856
    # a signed integer keeps its sign convention
    assert lv.swap_bytes(-2, "i16") == -257
    # an 8-bit integer, and the words of a 16-bit one, are left alone
    assert lv.swap_bytes(0x12, "u8") == 0x12
    assert lv.swap_words(0x1234, "u16") == 0x1234


def test_swap_acts_only_on_the_integers_of_a_cluster_and_each_array_element() -> None:
    spec = (
        "cluster",
        False,
        (("n", "u16"), ("x", "f32"), ("list", "u16[]"), ("label", "str")),
    )
    assert lv.swap_bytes((0x0102, 1.5, [0x0304, 0x0506], "ab"), spec) == (
        0x0201,
        1.5,
        [0x0403, 0x0605],
        "ab",
    )


def test_type_specs_of_wire_types() -> None:
    from lvkit.codegen.nodes.ops.type_cast import _spec
    from lvkit.models import ClusterField, LVType, LVTypeKind

    def primitive(name: str) -> LVType:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)

    bytes_ = LVType(
        kind=LVTypeKind.ARRAY, element_type=primitive("NumUInt8"), dimensions=1
    )
    grid = LVType(
        kind=LVTypeKind.ARRAY, element_type=primitive("NumFloat32"), dimensions=2
    )
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="data size", type=primitive("NumInt32")),
            ClusterField(name="ok", type=primitive("Boolean")),
            ClusterField(name="level", type=primitive("NumFloat64")),
            ClusterField(name="text", type=primitive("String")),
        ],
    )
    assert _spec(bytes_) == "u8[]"
    assert _spec(grid) == ("array", "f32", 2)
    assert _spec(cluster) == (
        "cluster",
        False,
        (("data_size", "i32"), ("ok", "bool"), ("level", "f64"), ("text", "str")),
    )
    byte_stream = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="ByteStream"
    )
    visa = LVType(
        kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum", ref_type="VisaRef"
    )
    assert _spec(byte_stream) == "refnum"
    assert _spec(visa) == "Refnum"  # flattened as a string: not supported


def test_integers_wrap_around_at_their_width() -> None:
    # generated arithmetic does not wrap, LabVIEW integers do
    assert lv.swap_bytes(0x1_1234_5678, "u32") == 0x34127856
    assert lv.type_cast(0x1_0000_0001, "u32", "u32") == 1

"""Arrays and numbers generate plain Python: each construct is generated on a
one-node diagram, its source is pinned, and the source is run.

The generated module imports nothing from ``lvkit``.
"""

from __future__ import annotations

import math

import pytest

from lvkit.codegen.errors import CodeGenError
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import Generated, array, generate, node, scalar

DBL = scalar("NumFloat64")
I32 = scalar("NumInt32")
I16 = scalar("NumInt16")
U32 = scalar("NumUInt32")
U8 = scalar("NumUInt8")
BOOL = scalar("Boolean")
STR = scalar("String")


def body(generated: Generated) -> str:
    """The generated VI function's statements, without the signature."""
    text = generated.source[generated.source.index("def native_test") :]
    return "\n".join(text.splitlines()[1:])


def run(generated: Generated, *arguments):
    return generated.function(*arguments)[0]


def no_lvkit(generated: Generated) -> None:
    assert "lvkit" not in generated.source


def binary(prim_id, left, right, result, names=("x", "y")):
    """A two-input numeric primitive: ``x`` is input 2 and ``y`` input 1."""
    return generate(
        {names[0]: left, names[1]: right},
        {"r": result},
        [
            node(
                "p",
                prim_id,
                [(0, "output", result), (1, "input", right), (2, "input", left)],
            )
        ],
        [(f"fp:{names[0]}", "p:2"), (f"fp:{names[1]}", "p:1"), ("p:0", "fp:r")],
    )


def value_and_count(prim_id, value, count, result):
    """Shift or Rotate: ``x`` is input 1 and the bit count ``y`` input 2."""
    return generate(
        {"x": value, "y": count},
        {"r": result},
        [
            node(
                "p",
                prim_id,
                [(0, "output", result), (1, "input", value), (2, "input", count)],
            )
        ],
        [("fp:x", "p:1"), ("fp:y", "p:2"), ("p:0", "fp:r")],
    )


def unary(prim_id, source, result):
    return generate(
        {"x": source},
        {"r": result},
        [node("p", prim_id, [(0, "output", result), (1, "input", source)])],
        [("fp:x", "p:1"), ("p:0", "fp:r")],
    )


# --- element-wise arithmetic, comparisons, conversions ---


def test_scalar_arithmetic_stays_the_plain_operator():
    generated = binary(1051, DBL, DBL, DBL)
    assert (
        body(generated).splitlines()[-1].strip() == "return NativeTestResult(r=x - y)"
    )
    assert run(generated, 5.0, 2.0) == 3.0
    no_lvkit(generated)


def test_array_minus_array_is_a_zip_comprehension():
    generated = binary(
        1051, array("NumFloat64"), array("NumFloat64"), array("NumFloat64")
    )
    assert "return NativeTestResult(r=[_e0 - _e1 for _e0, _e1 in zip(x, y)])" in body(
        generated
    )
    assert run(generated, [5.0, 7.0], [1.0, 2.0]) == [4.0, 5.0]
    no_lvkit(generated)


def test_scalar_and_array_broadcast_without_zip():
    generated = binary(1050, DBL, array("NumFloat64"), array("NumFloat64"))
    assert "[_e0 + x for _e0 in y]" in body(generated)
    assert run(generated, 1.0, [1.0, 2.0]) == [2.0, 3.0]
    no_lvkit(generated)


def test_two_dimensional_arrays_nest_one_comprehension_per_dimension():
    matrix = array("NumFloat64", 2)
    generated = binary(1051, matrix, matrix, matrix)
    assert (
        "[[_e2 - _e3 for _e2, _e3 in zip(_e0, _e1)] for _e0, _e1 in zip(x, y)]"
        in body(generated)
    )
    assert run(generated, [[5.0, 6.0], [7.0, 8.0]], [[1.0, 1.0], [2.0, 2.0]]) == [
        [4.0, 5.0],
        [5.0, 6.0],
    ]
    no_lvkit(generated)


def test_comparison_of_an_array_with_a_scalar():
    generated = binary(1102, array("NumInt32"), I32, array("Boolean"))
    assert "[y == _e0 for _e0 in x]" in body(generated)
    assert run(generated, [1, 2, 1], 1) == [True, False, True]
    no_lvkit(generated)


def test_negate_an_array():
    generated = unary(1069, array("NumInt32"), array("NumInt32"))
    assert "[-_e0 for _e0 in x]" in body(generated)
    assert run(generated, [1, -2]) == [-1, 2]


@pytest.mark.parametrize(
    "prim_id,target,ctype,value,expected",
    [
        (1141, "NumInt16", "c_int16", 40000.0, -25536),
        (1143, "NumUInt8", "c_uint8", 300.0, 44),
        (1144, "NumUInt16", "c_uint16", -1.0, 65535),
        (1145, "NumUInt32", "c_uint32", -1.0, 4294967295),
    ],
)
def test_integer_conversion_wraps_with_ctypes(prim_id, target, ctype, value, expected):
    generated = unary(prim_id, DBL, scalar(target))
    assert f"ctypes.{ctype}(round(x)).value" in body(generated)
    assert run(generated, value) == expected
    no_lvkit(generated)


def test_integer_conversion_rounds_half_to_even():
    generated = unary(1141, DBL, I16)
    assert [run(generated, v) for v in (0.5, 1.5, 2.5, -0.5)] == [0, 2, 2, 0]


def test_integer_conversion_of_an_array():
    generated = unary(1141, array("NumFloat64"), array("NumInt16"))
    assert "[ctypes.c_int16(round(_e0)).value for _e0 in x]" in body(generated)
    assert run(generated, [1.5, 40000.0, -2.5]) == [2, -25536, -2]


def test_plain_integer_arithmetic_is_not_wrapped():
    generated = binary(1050, I32, I32, I32)
    assert "ctypes" not in generated.source
    assert run(generated, 2**31 - 1, 1) == 2**31


@pytest.mark.parametrize(
    "x,shift,expected", [(0x80000001, 1, 2), (0x80000001, -31, 1), (3, 31, 0x80000000)]
)
def test_logical_shift_wraps_to_its_width(x, shift, expected):
    generated = value_and_count(1081, U32, I16, U32)
    assert "ctypes.c_uint32(" in body(generated)
    assert run(generated, x, shift) == expected


@pytest.mark.parametrize(
    "x,count,expected",
    [(0x80000001, 1, 3), (1, -1, 0x80000000), (5, 32, 5), (5, 0, 5), (1, 33, 2)],
)
def test_rotate_wraps_to_the_width_of_x(x, count, expected):
    generated = value_and_count(1082, U32, I32, U32)
    assert "ctypes.c_uint32(" in body(generated)
    assert run(generated, x, count) == expected
    no_lvkit(generated)


def test_log10_is_math_log10_over_each_element():
    generated = unary(1210, array("NumFloat64", 2), array("NumFloat64", 2))
    assert "[[math.log10(_e1) for _e1 in _e0] for _e0 in x]" in body(generated)
    assert run(generated, [[1.0, 10.0], [100.0, 1000.0]]) == [[0.0, 1.0], [2.0, 3.0]]
    no_lvkit(generated)


# --- float division ---


@pytest.mark.parametrize(
    "x,y,expected",
    [
        (6.0, 3.0, 2.0),
        (1.0, 0.0, math.inf),
        (-1.0, 0.0, -math.inf),
        (1.0, -0.0, -math.inf),
    ],
)
def test_float_divide_by_zero_is_infinity(x, y, expected):
    generated = binary(1053, DBL, DBL, DBL)
    assert "math.copysign(math.inf" in body(generated)
    assert run(generated, x, y) == expected
    no_lvkit(generated)


def test_float_zero_over_zero_is_nan():
    assert math.isnan(run(binary(1053, DBL, DBL, DBL), 0.0, 0.0))


def test_array_divide_keeps_infinity_at_zero_for_every_element():
    generated = binary(1053, array("NumFloat64"), DBL, array("NumFloat64"))
    assert "[_e0 / y if y else math.nan" in body(generated)
    assert run(generated, [4.0, 6.0], 2.0) == [2.0, 3.0]
    assert run(generated, [1.0, -1.0], 0.0) == [math.inf, -math.inf]


# --- Array Size, Index Array, Array Subset, Replace Array Subset ---


def test_array_size_one_dimension_is_len():
    generated = unary(1809, array("NumInt32"), I32)
    assert "size = len(x)" in body(generated)
    assert run(generated, [1, 2, 3]) == 3


def test_array_size_two_dimensions_is_a_list_of_lengths():
    matrix = array("NumInt32", 2)
    generated = unary(1809, matrix, array("NumInt32"))
    assert "size = [len(x), len(x[0]) if x else 0]" in body(generated)
    assert run(generated, [[1, 2, 3], [4, 5, 6]]) == [2, 3]
    assert run(generated, []) == [0, 0]
    no_lvkit(generated)


def index_array(element_array: LVType, element: LVType):
    return generate(
        {"values": element_array, "i": I32},
        {"r": element},
        [
            node(
                "ix",
                None,
                [
                    (0, "input", element_array),
                    (1, "output", element),
                    (2, "input", I32),
                ],
                "aIndx",
            )
        ],
        [("fp:values", "ix:0"), ("fp:i", "ix:2"), ("ix:1", "fp:r")],
    )


@pytest.mark.parametrize("index,expected", [(0, 1.0), (1, 2.0), (2, 0.0), (-1, 0.0)])
def test_index_array_out_of_range_is_the_element_default(index, expected):
    generated = index_array(array("NumFloat64"), DBL)
    assert "if 0 <= array_index_0 < len(indexed_array) else 0.0" in body(generated)
    assert run(generated, [1.0, 2.0], index) == expected
    no_lvkit(generated)


def test_index_array_of_a_row_out_of_range_is_an_empty_array():
    generated = index_array(array("NumFloat64", 2), array("NumFloat64"))
    assert "values[i] if 0 <= i < len(values) else []" in body(generated)
    assert run(generated, [[1.0], [2.0]], 1) == [2.0]
    assert run(generated, [[1.0], [2.0]], 5) == []


def array_subset(rank: int, with_length: bool):
    source = array("NumFloat64", rank)
    inputs = {"values": source, "i": I32}
    terminals = [(0, "input", source), (1, "output", source), (2, "input", I32)]
    wires = [("fp:values", "ss:0"), ("fp:i", "ss:2"), ("ss:1", "fp:r")]
    terminals.append((3, "input", I32))
    if with_length:
        inputs["n"] = I32
        wires.append(("fp:n", "ss:3"))
    return generate(
        {**inputs}, {"r": source}, [node("ss", None, terminals, "subset")], wires
    )


def test_array_subset_is_a_slice():
    generated = array_subset(1, True)
    assert "subarray = values[i:i + n]" in body(generated)
    assert run(generated, [1.0, 2.0, 3.0, 4.0], 1, 2) == [2.0, 3.0]
    assert run(generated, [1.0, 2.0, 3.0], 2, 10) == [3.0]
    no_lvkit(generated)


def test_array_subset_without_length_runs_to_the_end():
    generated = array_subset(1, False)
    assert "subarray = values[i:]" in body(generated)
    assert run(generated, [1.0, 2.0, 3.0, 4.0], 1) == [2.0, 3.0, 4.0]


def replace(array_rank: int, new: LVType, indices: int):
    source = array("NumFloat64", array_rank)
    inputs = {"values": source, "v": new}
    terminals = [(0, "input", source), (1, "output", source), (2, "input", new)]
    wires = [("fp:values", "rp:0"), ("fp:v", "rp:2"), ("rp:1", "fp:r")]
    for k in range(indices):
        inputs[f"i{k}"] = I32
        terminals.append((3 + k, "input", I32))
        wires.append((f"fp:i{k}", f"rp:{3 + k}"))
    return generate(
        inputs, {"r": source}, [node("rp", None, terminals, "aReplace")], wires
    )


def test_replace_element_writes_a_copy_inside_the_array():
    generated = replace(1, DBL, 1)
    assert "if 0 <= i0 < len(output_array):" in body(generated)
    original = [1.0, 2.0, 3.0]
    assert run(generated, original, 9.0, 1) == [1.0, 9.0, 3.0]
    assert original == [1.0, 2.0, 3.0]
    assert run(generated, original, 9.0, 3) == original
    assert run(generated, original, 9.0, -1) == original
    no_lvkit(generated)


def test_replace_subarray_is_cropped_to_fit():
    generated = replace(1, array("NumFloat64"), 1)
    assert run(generated, [1.0, 2.0, 3.0, 4.0], [8.0, 9.0], 1) == [1.0, 8.0, 9.0, 4.0]
    assert run(generated, [1.0, 2.0, 3.0], [7.0, 8.0, 9.0], 2) == [1.0, 2.0, 7.0]


def test_replace_element_of_a_matrix():
    generated = replace(2, DBL, 2)
    matrix = [[1.0, 2.0], [3.0, 4.0]]
    assert run(generated, matrix, 9.0, 1, 0) == [[1.0, 2.0], [9.0, 4.0]]
    assert run(generated, matrix, 9.0, 2, 0) == matrix
    assert matrix == [[1.0, 2.0], [3.0, 4.0]]


def test_replace_a_row_segment_of_a_matrix():
    generated = replace(2, array("NumFloat64"), 2)
    assert run(generated, [[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]], [8.0, 9.0], 1, 1) == [
        [1.0, 2.0, 3.0],
        [3.0, 8.0, 9.0],
    ]


# --- spreadsheet string to array ---


def spreadsheet(result: LVType):
    return generate(
        {"s": STR, "d": STR},
        {"r": result},
        [
            node(
                "sp",
                1539,
                [(0, "output", result), (1, "input", STR), (3, "input", STR)],
            )
        ],
        [("fp:s", "sp:3"), ("fp:d", "sp:1"), ("sp:0", "fp:r")],
    )


def test_spreadsheet_string_to_one_dimensional_strings():
    generated = spreadsheet(array("String"))
    assert "re.split(" in body(generated)
    assert run(generated, "a,b\nc", ",") == ["a", "b", "c"]
    assert run(generated, "", ",") == []
    assert run(generated, "a\tb", "") == ["a", "b"]
    no_lvkit(generated)


def test_spreadsheet_string_to_two_dimensional_integers():
    generated = spreadsheet(array("NumInt32", 2))
    assert run(generated, "1,2\n3,\n", ",") == [[1, 2], [3, 0]]


# --- bundle by position ---


def test_bundle_replaces_fields_of_an_anonymous_cluster():
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=I32), ClusterField(name="b", type=STR)],
    )
    assert cluster.fields is not None


# --- Type Cast, Unflatten From String, Swap Bytes / Words ---


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
    )


def unflatten(target: LVType):
    return generate(
        {"s": STR},
        {"v": target, "rest": STR},
        [
            node(
                "uf",
                1165,
                [
                    (2, "output", target),
                    (3, "output", STR),
                    (7, "input", target),
                    (11, "input", STR),
                ],
            )
        ],
        [("fp:s", "uf:11"), ("uf:2", "fp:v"), ("uf:3", "fp:rest")],
    )


RECORD = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[ClusterField(name="count", type=I32), ClusterField(name="label", type=STR)],
)


def test_type_cast_integer_to_string_is_struct_pack():
    generated = cast(U32, STR)
    assert "flattened = struct.pack('>I', ctypes.c_uint32(x).value)" in body(generated)
    assert run(generated, 0x41424344) == "ABCD"
    no_lvkit(generated)


def test_type_cast_string_to_integer_pads_with_zeros():
    generated = cast(STR, U8)
    assert run(generated, "A") == 65
    assert run(generated, "") == 0
    wide = cast(STR, U32)
    assert run(wide, "AB") == 0x41420000


def test_type_cast_array_of_integers_to_string_has_no_sizes():
    generated = cast(array("NumInt16"), STR)
    assert "struct.pack(f'>{len(x)}h'" in body(generated)
    assert run(generated, [1, -2]) == "\x00\x01\xff\xfe"


def test_type_cast_string_to_array_of_integers():
    generated = cast(STR, array("NumUInt16"))
    assert run(generated, "ABCD") == [0x4142, 0x4344]


def test_type_cast_cluster_both_ways():
    flatten = cast(RECORD, STR)
    assert run(flatten, (5, "ab")) == "\x00\x00\x00\x05\x00\x00\x00\x02ab"
    assert "def _flatten_cluster(value):" in flatten.source
    back = cast(STR, RECORD)
    assert run(back, "\x00\x00\x00\x05\x00\x00\x00\x02ab") == (5, "ab")
    assert "def _unflatten_cluster(data, pos):" in back.source
    no_lvkit(back)


def test_type_cast_of_a_type_with_no_flattened_form_stops_generation():
    with pytest.raises(CodeGenError, match="does not handle.*Variant"):
        cast(scalar("Variant"), U32)


EXTENDED_QUARTER = "3ffd" + "00" * 14


def test_extended_precision_is_a_float_in_the_128_bit_format():
    extended = scalar("NumFloatExt")
    flatten = cast(extended, STR)
    assert run(flatten, 0.25).encode("latin-1").hex() == EXTENDED_QUARTER
    assert run(flatten, -1.5).encode("latin-1").hex() == "bfff8" + "0" * 27
    assert run(flatten, 0.0).encode("latin-1").hex() == "00" * 16
    unflatten_text = bytes.fromhex(EXTENDED_QUARTER).decode("latin-1")
    assert run(cast(STR, extended), unflatten_text) == 0.25
    for value in (1.0, -3.75, 1e300, 5e-324, math.inf, -math.inf):
        encoded = flatten.function(value).r
        assert run(cast(STR, extended), encoded) == value
    assert math.isnan(run(cast(STR, extended), run(flatten, math.nan)))
    no_lvkit(flatten)


def test_extended_precision_complex_is_two_floats():
    complex_ext = scalar("NumComplexExt")
    flatten = cast(complex_ext, STR)
    encoded = run(flatten, complex(0.25, -1.5))
    assert len(encoded) == 32
    assert run(cast(STR, complex_ext), encoded) == complex(0.25, -1.5)


def test_unflatten_number_and_rest():
    generated = unflatten(U32)
    assert "struct.unpack_from('>I', flattened)[0]" in body(generated)
    assert tuple(generated.function("\x00\x00\x00\x07xyz")) == (7, "xyz")
    no_lvkit(generated)


def test_unflatten_string_reads_its_length_prefix():
    generated = unflatten(STR)
    assert tuple(generated.function("\x00\x00\x00\x02hiXX")) == ("hi", "XX")


def test_unflatten_one_dimensional_array():
    generated = unflatten(array("NumFloat64"))
    data = "\x00\x00\x00\x01" + "\x3f\xf0" + "\x00" * 6
    assert tuple(generated.function(data)) == ([1.0], "")


def test_unflatten_two_dimensional_array_reshapes_by_its_sizes():
    generated = unflatten(array("NumInt16", 2))
    data = "\x00\x00\x00\x02\x00\x00\x00\x02\x00\x01\x00\x02\x00\x03\x00\x04"
    assert tuple(generated.function(data)) == ([[1, 2], [3, 4]], "")
    no_lvkit(generated)


def test_unflatten_cluster_reads_its_numbers_in_one_call():
    record = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="a", type=I32),
            ClusterField(name="b", type=scalar("NumUInt16")),
            ClusterField(name="c", type=DBL),
            ClusterField(name="d", type=STR),
        ],
    )
    generated = unflatten(record)
    assert "struct.unpack_from('>iHd', data, pos)" in generated.source
    data = "\x00\x00\x00\x05\x00\x07" + "\x3f\xf0" + "\x00" * 6 + "\x00\x00\x00\x01Z"
    assert tuple(generated.function(data)) == ((5, 7, 1.0, "Z"), "")


def swap(data: LVType, prim_id: int):
    return generate(
        {"x": data},
        {"r": data},
        [node("sw", prim_id, [(0, "output", data), (1, "input", data)])],
        [("fp:x", "sw:1"), ("sw:0", "fp:r")],
    )


def test_swap_bytes_and_words_of_a_long():
    assert run(swap(U32, 1162), 0x11223344) == 0x22114433
    assert run(swap(U32, 1163), 0x11223344) == 0x33441122


def test_swap_bytes_of_an_array_of_words():
    generated = swap(array("NumUInt16"), 1162)
    assert run(generated, [0x1122, 0x3344]) == [0x2211, 0x4433]
    no_lvkit(generated)


def test_swap_words_of_a_cluster_swaps_only_its_integers():
    record = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="a", type=U32), ClusterField(name="b", type=STR)],
    )
    assert run(swap(record, 1163), (0x11223344, "x")) == (0x33441122, "x")


def test_unflatten_reads_byte_order_and_size_flag_from_constants():
    u16 = scalar("NumUInt16")
    generated = generate(
        {"s": STR},
        {"v": u16},
        [
            node(
                "uf",
                1165,
                [
                    (2, "output", u16),
                    (7, "input", u16),
                    (9, "input", u16),
                    (11, "input", STR),
                ],
            )
        ],
        [("fp:s", "uf:11"), ("c9", "uf:9"), ("uf:2", "fp:v")],
        constants={"c9": (2, u16)},
    )
    assert "struct.unpack_from('<H', flattened)[0]" in body(generated)
    assert generated.function("\x05\x00")[0] == 5


def test_unflatten_without_size_prefix_takes_the_whole_string():
    generated = generate(
        {"s": STR},
        {"v": STR, "rest": STR},
        [
            node(
                "uf",
                1165,
                [
                    (2, "output", STR),
                    (3, "output", STR),
                    (7, "input", STR),
                    (10, "input", BOOL),
                    (11, "input", STR),
                ],
            )
        ],
        [("fp:s", "uf:11"), ("c10", "uf:10"), ("uf:2", "fp:v"), ("uf:3", "fp:rest")],
        constants={"c10": (False, BOOL)},
    )
    assert tuple(generated.function("rawtext")) == ("rawtext", "")


def test_rotate_matches_the_documented_examples_in_eight_bits():
    generated = value_and_count(1082, U8, I32, U8)
    assert [run(generated, 3, n) for n in (1, 2, -3, 0, 9)] == [6, 12, 96, 3, 6]


def test_type_cast_raw_bytes_of_integers_floats_and_arrays():
    to_text = cast(array("NumUInt32"), STR)
    assert run(to_text, [0x12345678, 0xDEADBEEF]).encode("latin-1").hex() == (
        "12345678deadbeef"
    )
    back = cast(STR, array("NumUInt32"))
    assert run(back, to_text.function([1, 2]).r) == [1, 2]
    assert run(cast(I16, STR), 258).encode("latin-1").hex() == "0102"
    # NI's "Flattened Data": a double of 1/4 flattens to 3FD0 0000 0000 0000.
    assert run(cast(DBL, STR), 0.25).encode("latin-1").hex() == "3fd0000000000000"


def test_not_of_an_array_is_bitwise_for_integers_and_logical_for_booleans():
    unsigned = unary(1064, array("NumUInt8"), array("NumUInt8"))
    assert "[ctypes.c_uint8(~_e0).value for _e0 in x]" in body(unsigned)
    assert run(unsigned, [0, 1, 255]) == [255, 254, 0]
    signed = unary(1064, array("NumInt32"), array("NumInt32"))
    assert "[~_e0 for _e0 in x]" in body(signed)
    assert run(signed, [0, 5]) == [-1, -6]
    flags = unary(1064, array("Boolean"), array("Boolean"))
    assert "[not _e0 for _e0 in x]" in body(flags)
    assert run(flags, [True, False]) == [False, True]


def test_not_of_an_unsigned_integer_wraps_with_ctypes():
    generated = unary(1064, U32, U32)
    assert "ctypes.c_uint32(~x).value" in body(generated)
    assert run(generated, 5) == 0xFFFFFFFA
    signed = unary(1064, I32, I32)
    assert "~x" in body(signed) and "ctypes" not in signed.source
    assert run(signed, 5) == -6

"""Variant To Flattened String (8203) and Flattened String To Variant (8202) on
``Variant`` values: the types decided when the code is generated (the flattened
data is the variant's ``value``), the round trip, and the flattening of a
``Variant`` whose type is not known until the program runs."""

from __future__ import annotations

import pytest

from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from lvkit.type_string import encode
from tests.helpers import generated_variants
from tests.native_vi import Generated, array, generate, node, result_values, scalar

I16 = scalar("NumInt16")
I32 = scalar("NumInt32")
STR = scalar("String")
VARIANT = scalar("LVVariant")
TS = array("NumInt16")
TS_CONSTANT = LVType(
    kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=I16, dimensions=1
)


def record() -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(name="s", type=STR), ClusterField(name="n", type=I16)],
    )


def from_string(lv_type: LVType) -> Generated:
    """A VI whose Flattened String To Variant reads ``d`` with a constant type
    string describing ``lv_type``."""
    return generate(
        {"d": STR},
        {"v": VARIANT},
        [
            node(
                "fv",
                8202,
                [(3, "output", VARIANT), (6, "input", STR), (7, "input", TS)],
            )
        ],
        [("fp:d", "fv:6"), ("c", "fv:7"), ("fv:3", "fp:v")],
        constants={"c": (str(encode(lv_type)), TS_CONSTANT)},
    )


def to_string(lv_type: LVType) -> Generated:
    """A VI that makes a variant of its ``x`` and flattens it."""
    return generate(
        {"x": lv_type},
        {"t": TS, "d": STR},
        [
            node("tv", 8201, [(0, "output", VARIANT), (1, "input", lv_type)]),
            node(
                "vf",
                8203,
                [(0, "input", VARIANT), (1, "output", TS), (2, "output", STR)],
            ),
        ],
        [("fp:x", "tv:1"), ("tv:0", "vf:0"), ("vf:1", "fp:t"), ("vf:2", "fp:d")],
    )


def round_trip(lv_type: LVType) -> Generated:
    return generate(
        {"x": lv_type},
        {"v": VARIANT},
        [
            node("tv", 8201, [(0, "output", VARIANT), (1, "input", lv_type)]),
            node(
                "vf",
                8203,
                [(0, "input", VARIANT), (1, "output", TS), (2, "output", STR)],
            ),
            node(
                "fv",
                8202,
                [(3, "output", VARIANT), (6, "input", STR), (7, "input", TS)],
            ),
        ],
        [
            ("fp:x", "tv:1"),
            ("tv:0", "vf:0"),
            ("vf:1", "fv:7"),
            ("vf:2", "fv:6"),
            ("fv:3", "fp:v"),
        ],
    )


def test_the_catalog_names_both_nodes_and_their_ops() -> None:
    to_variant = get_resolver().resolve(prim_id=8202)
    to_string_entry = get_resolver().resolve(prim_id=8203)
    assert to_variant is not None and to_string_entry is not None
    assert to_variant.op == "FLATTENED_STRING_TO_VARIANT"
    assert to_string_entry.op == "VARIANT_TO_FLATTENED_STRING"
    assert {t.index: t.name for t in to_variant.terminals} == {
        0: "error out",
        3: "Variant",
        4: "error in",
        6: "data string",
        7: "type string",
    }
    assert {t.index: t.name for t in to_string_entry.terminals} == {
        0: "Variant",
        1: "type string",
        2: "data string",
    }


def test_a_constant_type_string_reads_a_number() -> None:
    generated = from_string(I32)
    variants = generated_variants()
    assert result_values(generated.function("\x00\x00\x00\x07")) == (
        variants.Variant(7, variants.LV_I32),
    )
    assert "lvkit" not in generated.source and "pickle" not in generated.source


def test_a_constant_type_string_reads_a_cluster() -> None:
    generated = from_string(record())
    held = result_values(generated.function("\x00\x00\x00\x03ABC\x00\x04"))[0]
    assert held.type.code == 0x50
    value = held.value
    fields = (value.s, value.n) if hasattr(value, "s") else tuple(value)
    assert fields == ("ABC", 4)


def test_a_typed_variant_flattens_with_its_type_and_writes_its_type_string() -> None:
    generated = to_string(I32)
    # A type known when the code is generated flattens the variant's value.
    assert ".value" in generated.source and "pickle" not in generated.source
    type_string, data = result_values(generated.function(7))
    assert type_string == [4, 3]
    assert data.encode("latin-1").hex() == "00000007"
    assert "pickle" not in generated.source


def test_a_typed_cluster_flattens_as_nis_flattened_data_page() -> None:
    type_string, data = result_values(to_string(record()).function(("ABC", 4)))
    assert type_string == encode(record())
    assert data.encode("latin-1").hex() == "000000034142430004"


def test_the_round_trip_hands_back_the_value() -> None:
    variants = generated_variants()
    assert result_values(round_trip(I32).function(7)) == (
        variants.Variant(7, variants.LV_I32),
    )
    (held,) = result_values(round_trip(array("NumInt32")).function([1, 2, 3]))
    assert (held.value, held.type.code) == ([1, 2, 3], 0x40)
    assert "pickle" not in round_trip(I32).source


def unknown_type_flatten() -> Generated:
    """A VI that flattens a variant its caller supplies, of a type the diagram
    does not know."""
    return generate(
        {"v": VARIANT},
        {"t": TS, "d": STR},
        [
            node(
                "vf",
                8203,
                [(0, "input", VARIANT), (1, "output", TS), (2, "output", STR)],
            )
        ],
        [("fp:v", "vf:0"), ("vf:1", "fp:t"), ("vf:2", "fp:d")],
    )


def unknown_type_round_trip() -> Generated:
    return generate(
        {"v": VARIANT},
        {"r": VARIANT},
        [
            node(
                "vf",
                8203,
                [(0, "input", VARIANT), (1, "output", TS), (2, "output", STR)],
            ),
            node(
                "fv",
                8202,
                [(3, "output", VARIANT), (6, "input", STR), (7, "input", TS)],
            ),
        ],
        [("fp:v", "vf:0"), ("vf:1", "fv:7"), ("vf:2", "fv:6"), ("fv:3", "fp:r")],
    )


RUN_TIME_CASES = [
    (I32, 42),
    (scalar("NumFloat64"), 2.5),
    (STR, "hello"),
    (scalar("Boolean"), True),
    (array("NumInt32"), [1, 2, 3]),
    (array("NumInt32", 2), [[1, 2, 3], [4, 5, 6]]),
    (record(), ("ABC", 4)),
    (
        LVType(
            LVTypeKind.ENUM,
            underlying_type="UnitUInt8",
            values={"a": EnumValue(value=0), "b": EnumValue(value=1)},
        ),
        1,
    ),
]


@pytest.mark.parametrize(("lv_type", "value"), RUN_TIME_CASES)
def test_a_variant_of_unknown_type_flattens_as_the_type_it_records(
    lv_type: LVType, value: object
) -> None:
    flatten = unknown_type_flatten()
    assert "pickle" not in flatten.source and "lvkit" not in flatten.source
    variants = generated_variants()
    words = encode(lv_type)
    given = variants.Variant(value, variants.type_from_string(words))
    type_string, data = result_values(flatten.function(given))
    # The type string is the descriptor the reader expects, and the data string
    # is the bytes the generation-time writer (Flatten To String's) gives.
    assert [w & 0xFFFF for w in type_string] == [w & 0xFFFF for w in words]
    if lv_type.kind != LVTypeKind.ARRAY or lv_type.dimensions == 1:
        _, static_data = result_values(to_string(lv_type).function(value))
        assert data == static_data
    (back,) = result_values(unknown_type_round_trip().function(given))
    assert back.value == value or list(back.value) == list(value)
    assert back.type.type_string == given.type.type_string


def test_a_variant_of_unknown_type_with_no_writable_type_raises_naming_it() -> None:
    flatten = unknown_type_flatten()
    variants = generated_variants()
    with pytest.raises(NotImplementedError, match="holds no type"):
        flatten.function(variants.Variant())
    bare = variants.Variant(1, variants.LVType(0x70, "Refnum", None))
    with pytest.raises(NotImplementedError, match="'Refnum', which records no"):
        flatten.function(bare)
    refnum = variants.Variant(1, variants.type_from_string([4, 0x70]))
    with pytest.raises(NotImplementedError, match="is not written"):
        flatten.function(refnum)


def test_labview_bytes_with_a_run_time_type_string_are_read_by_that_type() -> None:
    """A data string that did not come from a pickling Variant To Flattened
    String holds LabVIEW's bytes (OpenG Get Default Data from TD reads a Type
    Cast): they are read by the type string when the code runs, never pickle; a
    type the reader does not know raises naming it."""
    generated = generate(
        {"d": STR, "t": TS},
        {"v": VARIANT},
        [
            node(
                "fv",
                8202,
                [(3, "output", VARIANT), (6, "input", STR), (7, "input", TS)],
            )
        ],
        [("fp:d", "fv:6"), ("fp:t", "fv:7"), ("fv:3", "fp:v")],
    )
    assert "pickle.loads" not in generated.source
    variants = generated_variants()
    (read,) = result_values(generated.function("\x00\x00\x00\x07", [4, 3]))
    assert read == variants.Variant(7, variants.LV_I32)
    (cluster,) = result_values(
        generated.function("\x00\x00\x00\x03ABC\x00\x04", encode(record()))
    )
    assert cluster.value == ("ABC", 4)
    with pytest.raises(NotImplementedError, match="not read"):
        generated.function("\x00\x00\x00\x00", [4, 0x54])


def test_labview_bytes_with_no_type_string_wired_are_a_gap() -> None:
    generated = generate(
        {"d": STR},
        {"v": VARIANT},
        [node("fv", 8202, [(3, "output", VARIANT), (6, "input", STR)])],
        [("fp:d", "fv:6"), ("fv:3", "fp:v")],
    )
    with pytest.raises(NotImplementedError, match="no type string wired"):
        generated.function("\x00\x00\x00\x07")


def test_a_known_type_with_no_type_string_is_a_gap_that_raises_where_it_runs() -> None:
    generated = to_string(scalar("Path"))
    with pytest.raises(NotImplementedError, match="no type string for.*Path"):
        generated.function(None)

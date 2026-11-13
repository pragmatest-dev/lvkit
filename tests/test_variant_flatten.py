"""Variant To Flattened String (8203) and Flattened String To Variant (8202) on
``Variant`` values: the types decided when the code is generated (the flattened
data is the variant's ``value``), the round trip, and the pickle of the whole
``Variant`` used for a variant whose type is not known until the program runs."""

from __future__ import annotations

import ast

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
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


def test_a_variant_of_unknown_type_is_pickled_and_read_back_by_pickle() -> None:
    flatten = generate(
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
    assert "pickle.dumps" in flatten.source
    variants = generated_variants()
    given = variants.Variant(
        {"a": [1, 2.5]},
        variants.LV_I32,
        {"note": variants.Variant("x", variants.LV_String)},
    )
    type_string, _ = result_values(flatten.function(given))
    assert type_string is None
    # The pickled data string, read back with a type string known only at run
    # time, is unpickled.
    both = generate(
        {"v": VARIANT, "t": TS},
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
        [("fp:v", "vf:0"), ("vf:2", "fv:6"), ("fp:t", "fv:7"), ("fv:3", "fp:r")],
    )
    assert "pickle.loads" in both.source
    # The whole Variant, with its type and attributes, comes back equal.
    (back,) = result_values(both.function(given, [4, 3]))
    assert back == given and back is not given
    ast.parse(both.source)


def test_labview_bytes_with_a_run_time_type_string_are_a_gap() -> None:
    """A data string that did not come from a pickling Variant To Flattened
    String holds LabVIEW's bytes (OpenG Get Default Data from TD reads a Type
    Cast): with no type known when generating, that is a gap, never pickle."""
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
    with pytest.raises(NotImplementedError, match="LabVIEW's flattened bytes"):
        generated.function("\x00\x00\x00\x07", [4, 3])


def test_a_known_type_with_no_flattened_form_is_a_gap_that_raises_where_it_runs() -> (
    None
):
    generated = to_string(scalar("Path"))
    with pytest.raises(NotImplementedError, match="does not handle.*Path"):
        generated.function(None)

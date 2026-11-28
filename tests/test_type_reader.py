"""The reader of a variant's type string that a generated package's ``variants``
module holds is the inverse of ``type_string.encode``: for every kind of type the
encoder writes, reading what it wrote gives that type's parts."""

from __future__ import annotations

import pytest

from lvkit import type_string
from lvkit.lv_type_codes import TD_FULL_TYPE
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from tests.helpers import generated_variants


def _scalar(name: str) -> LVType:
    return LVType(LVTypeKind.PRIMITIVE, underlying_type=name)


def _array(element: LVType, dimensions: int = 1) -> LVType:
    return LVType(
        LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=element,
        dimensions=dimensions,
    )


def _cluster(*members: LVType) -> LVType:
    return LVType(
        LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(f"f{i}", m, labelled=False) for i, m in enumerate(members)
        ],
    )


def _enum(*labels: str) -> LVType:
    return LVType(
        LVTypeKind.ENUM,
        underlying_type="UnitUInt8",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
    )


def _runtime(lv_type: LVType, v):
    """The ``LVType`` a generated variant records for ``lv_type``."""
    words = tuple(type_string.encode(lv_type))
    return v.LVType(int(TD_FULL_TYPE[lv_type.underlying_type].value), "x", words)


@pytest.mark.parametrize(
    "name",
    [
        "NumInt8", "NumInt16", "NumInt32", "NumInt64", "NumUInt8", "NumUInt16",
        "NumUInt32", "NumUInt64", "NumFloat32", "NumFloat64", "NumFloatExt",
        "NumComplex64", "NumComplex128", "NumComplexExt",
    ],
)
def test_every_number_reads_as_its_name(name: str) -> None:
    v = generated_variants()
    info = v.read_type(_runtime(_scalar(name), v))
    assert (info.kind, info.number) == ("number", name)


def test_a_boolean_and_a_string() -> None:
    v = generated_variants()
    assert v.read_type(_runtime(_scalar("Boolean"), v)).kind == "boolean"
    assert v.read_type(_runtime(_scalar("String"), v)).kind == "string"


def test_an_array_reads_its_dimensions_and_element() -> None:
    v = generated_variants()
    one = v.read_type(_runtime(_array(_scalar("NumFloat64")), v))
    assert one.kind == "array"
    assert one.dimensions == ((True, 0),)
    assert one.element == v.LV_DBL
    two = v.read_type(_runtime(_array(_scalar("Boolean"), 2), v))
    assert two.dimensions == ((True, 0), (True, 0))
    assert two.element == v.LV_TF
    assert v.read_type(_runtime(_array(_scalar("String")), v)).element == v.LV_String


def test_an_array_of_clusters_reads_the_cluster_as_its_element() -> None:
    v = generated_variants()
    cluster = _cluster(_scalar("NumInt32"), _scalar("String"))
    info = v.read_type(_runtime(_array(cluster), v))
    assert info.element.code == 0x50
    assert info.element.type_string == tuple(type_string.encode(cluster))
    inner = v.read_type(info.element)
    assert inner.fields == (v.LV_I32, v.LV_String)


def test_a_cluster_reads_its_members_in_order_including_nested_ones() -> None:
    v = generated_variants()
    nested = _cluster(_scalar("NumInt16"), _array(_scalar("NumUInt8")))
    outer = _cluster(_scalar("Boolean"), nested, _enum("a", "b"))
    info = v.read_type(_runtime(outer, v))
    assert info.kind == "cluster"
    assert [f.code for f in info.fields] == [0x21, 0x50, 0x15]
    assert v.read_type(info.fields[1]).fields[0] == v.LV_I16
    assert v.read_type(info.fields[1]).fields[1].code == 0x40
    assert v.read_type(info.fields[2]).items == ("a", "b")


@pytest.mark.parametrize(
    "labels",
    [("am", "fm", "fm stereo"), ("one",), ("a", "bc"), ("", "x")],
)
def test_an_enum_reads_its_items(labels: tuple[str, ...]) -> None:
    v = generated_variants()
    info = v.read_type(_runtime(_enum(*labels), v))
    assert info.kind == "enum"
    assert info.items == labels


def test_nis_documented_enum_descriptor_is_read() -> None:
    v = generated_variants()
    words = tuple(
        w - 0x10000 if w & 0x8000 else w
        for w in (0x0016, 0x0015, 0x0003, 0x0261, 0x6D02, 0x666D, 0x0966, 0x6D20,
                  0x7374, 0x6572, 0x656F)
    )
    info = v.read_type(v.LVType(0x15, "x", words))
    assert info.items == ("am", "fm", "fm stereo")


def test_a_name_after_the_type_specific_info_is_read() -> None:
    v = generated_variants()
    # A 32-bit integer (size 4 + name 4 = 8): 0008 0003 0361 6263 ("abc").
    info = v.read_type(v.LVType(3, "x", (8, 3, 0x0361, 0x6263)))
    assert (info.kind, info.name) == ("number", "abc")


def test_a_fixed_size_dimension_is_read_with_its_size() -> None:
    v = generated_variants()
    # 1-D array of I32, dimension 5: 0010 0040 0001 0000 0005 0004 0003
    info = v.read_type(v.LVType(0x40, "x", (14, 0x40, 1, 0, 5, 4, 3)))
    assert info.dimensions == ((False, 5),)


def test_a_type_without_a_type_string_is_read_from_its_code() -> None:
    v = generated_variants()
    assert v.read_type(v.LVType(3, "I32", None)).kind == "number"
    assert v.read_type(v.LVType(0x70, "Refnum", None)).kind == "refnum"
    assert v.read_type(None).kind == "void"
    with pytest.raises(NotImplementedError, match="records no type string"):
        v.read_type(v.LVType(0x40, "Array", None))


def test_a_malformed_string_is_refused() -> None:
    v = generated_variants()
    with pytest.raises(ValueError, match="cannot hold"):
        v.read_type(v.LVType(0x50, "x", (30, 0x50, 1)))


def test_the_default_of_a_type() -> None:
    v = generated_variants()
    assert v.type_default(v.LV_DBL) == 0.0
    assert v.type_default(v.LV_I32) == 0
    assert v.type_default(v.LV_TF) is False
    assert v.type_default(v.LV_String) == ""
    assert v.type_default(_runtime(_array(_scalar("NumInt32")), v)) == []


def test_the_default_of_a_cluster_is_its_members_defaults() -> None:
    """Get Array Information's element is a value of the element type: a cluster
    of its members' defaults, a Variant member an empty variant."""
    v = generated_variants()
    inner = _cluster(_scalar("String"), _scalar("NumFloat64"))
    outer = _cluster(inner, _scalar("NumInt32"), _scalar("LVVariant"))
    runtime = v.LVType(80, "x", tuple(type_string.encode(outer)))
    assert v.type_default(runtime) == (("", 0.0), 0, v.Variant())
    assert v.read_type(runtime).fields[2].code == 83


def test_a_variant_member_is_recorded_as_a_descriptor_with_no_body() -> None:
    assert type_string.encode(_scalar("LVVariant")) == [4, 83]
    decoded = type_string.decode(type_string.encode(_cluster(_scalar("LVVariant"))))
    assert decoded.fields is not None
    assert decoded.fields[0].type is not None
    assert decoded.fields[0].type.underlying_type == "LVVariant"

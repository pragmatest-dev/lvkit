"""A ring's items reach its type, so every view can name them.

The VI's type map records a ring control or constant as its bare integer
(``U16``, ``I32``): unlike an enum's, a ring's items are not part of its type.
They live only in the ring's heap ddo -- the labels in its ``multiLabel``
``<buf>``, and, when they differ from the ordinals, the item values in
``<ringSparseValues>``.

Regression: describe/lvnet/JSON showed every ring as its integer
(``MC_Options : U16``), so the item names were lost, and a ring constant fed
into a .NET method came out as a bare number (``= 4`` for ``Ethernet``). The
labels were parsed but never reached the type; the sparse values were not read
at all, so a ring whose items stand for 0, 1024 and 8192 had no way to say so.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from lvkit.graph.construction import _control_ring_type, _ring_type, decode_constant
from lvkit.graph.render_lvnet import lvnet_type_lossless_def
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from lvkit.parser.fp_heap_type import ring_sparse_values
from lvkit.parser.models import ParsedBlockDiagram
from lvkit.parser.nodes.constant import extract_constants
from lvkit.parser.vi import _parse_front_panel
from lvkit.render.style import numeric_repr, type_repr

_SPARSE = (
    '<ringSparseValues elements="3">'
    "<SL__arrayElement>00</SL__arrayElement>"
    "<SL__arrayElement>0400</SL__arrayElement>"
    "<SL__arrayElement>2000</SL__arrayElement>"
    "</ringSparseValues>"
)
_ITEMS = '(3)"Filter""No filter""Wait for new data"'


def _ring_ddo(uid: str, items: str, sparse: str = "") -> str:
    return (
        f'<ddo class="stdRing" uid="{uid}">'
        "<partsList>"
        f'<SL__arrayElement class="label" uid="{uid}1">'
        '<partID>16</partID><textRec class="textHair"><text>"Options"</text></textRec>'
        "</SL__arrayElement>"
        f'<SL__arrayElement class="multiLabel" uid="{uid}2">'
        f"<partID>12</partID><buf>{items}</buf>"
        "</SL__arrayElement>"
        "</partsList>"
        f"{sparse}"
        "</ddo>"
    )


def _u16() -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16")


# --- reading the heap -------------------------------------------------------


def test_sparse_values_decode_as_signed_big_endian_minimal_length() -> None:
    ring = ET.fromstring(
        '<ddo class="stdRing"><ringSparseValues elements="4">'
        "<SL__arrayElement>00</SL__arrayElement>"
        "<SL__arrayElement>0400</SL__arrayElement>"
        "<SL__arrayElement>FF</SL__arrayElement>"
        "<SL__arrayElement>008000</SL__arrayElement>"
        "</ringSparseValues></ddo>"
    )
    assert ring_sparse_values(ring) == [0, 1024, -1, 32768]


def test_ring_without_sparse_values_has_none() -> None:
    assert ring_sparse_values(ET.fromstring('<ddo class="stdRing" />')) == []


def test_unreadable_sparse_value_drops_the_list() -> None:
    ring = ET.fromstring(
        '<ddo class="stdRing"><ringSparseValues elements="2">'
        "<SL__arrayElement>01</SL__arrayElement>"
        "<SL__arrayElement>zz</SL__arrayElement>"
        "</ringSparseValues></ddo>"
    )
    assert ring_sparse_values(ring) == []


def test_front_panel_ring_control_carries_labels_and_values(tmp_path: Path) -> None:
    fp_xml = tmp_path / "Ring_FPHb.xml"
    fp_xml.write_text(
        '<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="2">'
        '<ddoList><SL__arrayElement uid="10" /></ddoList>'
        '<paneHierarchy class="pane" uid="3"><zPlaneList>'
        '<SL__arrayElement class="fPDCO" uid="10">'
        + _ring_ddo("11", _ITEMS, _SPARSE)
        + "</SL__arrayElement>"
        "</zPlaneList></paneHierarchy></root></SL__rootObject>"
    )
    fp = _parse_front_panel(
        fp_xml, ParsedBlockDiagram(nodes=[], constants=[], wires=[])
    )

    (ring,) = fp.controls
    assert ring.control_type == "stdRing"
    assert ring.enum_values == ["Filter", "No filter", "Wait for new data"]
    assert ring.ring_values == [0, 1024, 8192]


def test_ring_constant_carries_labels_and_values() -> None:
    root = ET.fromstring(
        "<root><nodeList>"
        '<SL__arrayElement class="term" uid="50">'
        '<dco class="bDConstDCO" uid="51"><typeDesc>TypeID(1)</typeDesc>'
        + _ring_ddo(
            "52",
            '(4)"Usb""Bluetooth""Ethernet""Any"',
            '<ringSparseValues elements="4">'
            "<SL__arrayElement>01</SL__arrayElement>"
            "<SL__arrayElement>02</SL__arrayElement>"
            "<SL__arrayElement>04</SL__arrayElement>"
            "<SL__arrayElement>07</SL__arrayElement>"
            "</ringSparseValues>",
        )
        + "<ConstValue>00000004</ConstValue>"
        "</dco></SL__arrayElement>"
        "</nodeList></root>"
    )
    (const,) = extract_constants(root)
    assert const.ring_labels == ["Usb", "Bluetooth", "Ethernet", "Any"]
    assert const.ring_values == [1, 2, 4, 7]


def test_typedef_wrapped_ring_constant_is_unwrapped() -> None:
    root = ET.fromstring(
        "<root><nodeList>"
        '<SL__arrayElement class="term" uid="50">'
        '<dco class="bDConstDCO" uid="51"><typeDesc>TypeID(1)</typeDesc>'
        '<ddo class="typeDef" uid="52">'
        + _ring_ddo("53", '(2)"Off""On"')
        + "</ddo><ConstValue>0001</ConstValue>"
        "</dco></SL__arrayElement>"
        "</nodeList></root>"
    )
    (const,) = extract_constants(root)
    assert const.ring_labels == ["Off", "On"]
    assert const.ring_values == []


def test_non_ring_constant_has_no_ring_items() -> None:
    root = ET.fromstring(
        "<root><nodeList>"
        '<SL__arrayElement class="term" uid="50">'
        '<dco class="bDConstDCO" uid="51"><typeDesc>TypeID(1)</typeDesc>'
        '<ddo class="stdNum" uid="52" /><ConstValue>00000004</ConstValue>'
        "</dco></SL__arrayElement>"
        "</nodeList></root>"
    )
    (const,) = extract_constants(root)
    assert const.ring_labels == []
    assert const.ring_values == []


# --- building the type ------------------------------------------------------


def test_integer_becomes_ring_with_sparse_values() -> None:
    ring = _ring_type(
        _u16(), ["Filter", "No filter", "Wait for new data"], [0, 1024, 8192]
    )

    assert ring is not None
    assert ring.kind == LVTypeKind.RING
    assert ring.underlying_type == "NumUInt16"  # representation kept
    assert {n: ev.value for n, ev in (ring.values or {}).items()} == {
        "Filter": 0,
        "No filter": 1024,
        "Wait for new data": 8192,
    }
    assert ring.type_descriptor() == "ring{Filter, No filter, Wait for new data}"
    assert ring.member_name(1024) == "No filter"


def test_ring_without_sparse_values_uses_ordinals() -> None:
    ring = _ring_type(_u16(), ["Off", "On"], [])
    assert ring is not None
    assert {n: ev.value for n, ev in (ring.values or {}).items()} == {"Off": 0, "On": 1}


def test_mismatched_sparse_values_fall_back_to_ordinals() -> None:
    ring = _ring_type(_u16(), ["Off", "On"], [5])
    assert ring is not None
    assert [ev.value for ev in (ring.values or {}).values()] == [0, 1]


def test_types_that_are_not_integers_are_left_alone() -> None:
    enum = LVType(
        kind=LVTypeKind.ENUM,
        underlying_type="UnitUInt16",
        values={"A": EnumValue(0)},
    )
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    dbl = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
    for lv_type in (enum, string, dbl, None):
        assert _ring_type(lv_type, ["x", "y"], []) is lv_type
    assert _ring_type(_u16(), [], []).kind == LVTypeKind.PRIMITIVE


def test_array_of_rings_gets_a_ring_element() -> None:
    arr = LVType(kind=LVTypeKind.ARRAY, element_type=_u16(), dimensions=1)
    out = _ring_type(arr, ["Off", "On"], [])
    assert out is not None and out is not arr
    assert out.element_type is not None
    assert out.element_type.kind == LVTypeKind.RING
    assert arr.element_type is not None
    assert arr.element_type.kind == LVTypeKind.PRIMITIVE  # input untouched


def _ctrl(**kw: object) -> SimpleNamespace:
    return SimpleNamespace(
        enum_values=kw.get("enum_values", []),
        ring_values=kw.get("ring_values", []),
        children=kw.get("children", []),
    )


def test_cluster_fields_get_their_field_controls_ring_items() -> None:
    cluster = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField("TempRejFreq", _u16()),
            ClusterField(
                "Gain", LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
            ),
        ],
    )
    ctrl = _ctrl(
        children=[
            _ctrl(enum_values=["50 Hz", "60 Hz"], ring_values=[50, 60]),
            _ctrl(),
        ]
    )
    out = _control_ring_type(cluster, ctrl)

    assert out is not None and out.fields is not None
    freq, gain = out.fields
    assert freq.type is not None and freq.type.kind == LVTypeKind.RING
    assert freq.type.member_name(60) == "60 Hz"
    assert gain.type is cluster.fields[1].type  # untouched field kept as is


def test_cluster_with_mismatched_field_controls_is_left_alone() -> None:
    cluster = LVType(kind=LVTypeKind.CLUSTER, fields=[ClusterField("A", _u16())])
    assert _control_ring_type(cluster, _ctrl(children=[])) is cluster


def test_ring_constant_value_still_decodes_as_its_integer() -> None:
    ring = _ring_type(
        LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
        ["Usb", "Bluetooth", "Ethernet", "Any"],
        [1, 2, 4, 7],
    )
    const = SimpleNamespace(value="00000004")
    _, value, _, _ = decode_constant(const, lv_type=ring)  # type: ignore[arg-type]
    assert value == "4"
    assert ring is not None and ring.member_name(int(value)) == "Ethernet"


# --- the views --------------------------------------------------------------


def test_lvnet_names_ring_items_with_their_values() -> None:
    ring = _ring_type(
        _u16(), ["Filter", "No filter", "Wait for new data"], [0, 1024, 8192]
    )
    assert ring is not None
    assert (
        lvnet_type_lossless_def(ring)
        == "Ring{ Filter = 0, No filter = 1024, Wait for new data = 8192 }"
    )


def test_diagram_still_draws_a_ring_as_its_integer() -> None:
    ring = _ring_type(_u16(), ["Off", "On"], [])
    assert numeric_repr(ring) == "NumUInt16"
    assert type_repr(ring) == type_repr(_u16())

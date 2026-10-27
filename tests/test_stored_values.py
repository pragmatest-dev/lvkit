"""Stored front-panel defaults and diagram constants, read to their exact size.

Hand-built bytes for each layout (NI "Flattened Data" and "Type Descriptors":
https://docs-be.ni.com/api/bundle/labview/page/flattened-data.html), then the
corpus audit: every value of a fixed set of VIs, covering each kind of value
whose reading once came up short, consumed to exactly its stored byte count.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from lvkit.codegen.context import _format_constant
from lvkit.codegen.errors import CodeGenError
from lvkit.extractor import resolve_extracted
from lvkit.graph.construction import decode_constant
from lvkit.graph.models import Constant
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    UndecodedPicture,
    UnreadPrivateData,
    ValuesNotKept,
    find_unwritable,
    unique_names,
)
from lvkit.parser.audit import audit_vi, constant_padding
from lvkit.parser.models import ParsedConstant
from lvkit.parser.type_mapping import parse_vctp_types
from lvkit.parser.vi import MAX_STORED_ARRAY_ELEMENTS, _decode_element

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"


def _u32(*values: int) -> bytes:
    return b"".join(v.to_bytes(4, "big") for v in values)


def _string(text: str) -> bytes:
    return _u32(len(text)) + text.encode()


def _prim(name: str, **kw) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name, **kw)


def _array(element: LVType, dimensions: int = 1) -> LVType:
    return LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=element,
        dimensions=dimensions,
    )


def _cluster(*fields: tuple[str, LVType]) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(name=n, type=t) for n, t in fields],
    )


# === Text tables (a [[String]] control, as a LabVIEW project stores them) ===


def test_a_196_by_2_string_table_reads_every_cell():
    rows = [("CRE_Energy", "100000,000000"), ("CRE_Exponent", "10,000000")]
    rows += [(f"Setting_{n}", f"{n},000000") for n in range(194)]
    data = _u32(196, 2) + b"".join(_string(a) + _string(b) for a, b in rows)
    value, consumed, structured = _decode_element(data, _array(_prim("String"), 2))
    assert consumed == len(data)
    assert value.startswith("[['CRE_Energy', '100000,000000'], ['CRE_Exponent'")
    assert len(structured) == 196
    assert structured[1] == ["'CRE_Exponent'", "'10,000000'"]


# === Variants ===

_EMPTY_VARIANT = bytes.fromhex("0900800000000001000400000001000000000000")


def test_an_empty_variant_is_twenty_bytes():
    assert _decode_element(_EMPTY_VARIANT, _prim("LVVariant")) == (
        "Variant()",
        20,
        "Variant()",
    )


def test_a_variant_with_a_value_shows_it():
    data = (
        bytes.fromhex("09008000")
        + _u32(2)
        + bytes.fromhex("00040000")  # type 0: Void
        + bytes.fromhex("00040003")  # type 1: 32-bit integer
        + bytes.fromhex("0001")  # has a value
        + bytes.fromhex("0001")  # of type 1
        + _u32(42)
        + _u32(0)  # no attributes
    )
    value, consumed, _ = _decode_element(data, _prim("LVVariant"))
    assert (value, consumed) == ("Variant(42)", len(data))


def test_a_variant_attribute_is_another_variant():
    data = _EMPTY_VARIANT[:-4] + _u32(1) + _string("units") + _EMPTY_VARIANT
    assert _decode_element(data, _prim("LVVariant"))[1] == len(data)


def test_a_variant_shows_its_attributes():
    data = _EMPTY_VARIANT[:-4] + _u32(1) + _string("units") + _EMPTY_VARIANT
    value, _, _ = _decode_element(data, _prim("LVVariant"))
    assert value == "Variant(attributes={'units': Variant()})"


def test_variants_in_a_cluster_do_not_swallow_the_next_field():
    lv_type = _cluster(("v", _prim("LVVariant")), ("after", _prim("NumUInt8")))
    data = _EMPTY_VARIANT + bytes([9])
    _, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured == {"v": "Variant()", "after": "9"}


# === Extended-precision numbers ===


@pytest.mark.parametrize(
    "head,expected",
    [("3fff", 1.0), ("c000", -2.0), ("3ffe", 0.5), ("0000", 0.0)],
)
def test_an_extended_number_is_a_16_byte_quad(head, expected):
    data = bytes.fromhex(head) + bytes(14)
    value, consumed, _ = _decode_element(data, _prim("NumFloatExt"))
    assert (float(value), consumed) == (expected, 16)


def test_extended_infinity_and_not_a_number():
    inf = bytes.fromhex("7fff") + bytes(14)
    nan = bytes.fromhex("7fff8000") + bytes(12)
    assert _decode_element(inf, _prim("NumFloatExt"))[0] == "inf"
    assert math.isnan(float(_decode_element(nan, _prim("NumFloatExt"))[0]))


def test_a_complex_extended_number_is_two_quads():
    data = bytes.fromhex("3fff") + bytes(14) + bytes.fromhex("c000") + bytes(14)
    value, consumed, _ = _decode_element(data, _prim("NumComplexExt"))
    assert (value, consumed) == ("(1-2j)", 32)


def test_a_number_with_a_unit_flattens_as_the_number():
    data = struct.pack(">d", 2.5)
    assert _decode_element(data, _prim("UnitFloat64")) == ("2.5", 8, "2.5")


# === Refnums that store a tag, Sets and Maps ===


def test_a_tag_refnum_is_its_flattened_string():
    lv_type = _prim("Refnum", ref_type="VisaRef")
    assert _decode_element(_string("COM1"), lv_type) == ("'COM1'", 8, "'COM1'")


def test_a_flattened_tag_refnum_carries_three_more_fields():
    lv_type = _prim("Refnum", ref_type="UsrDefTagFlt")
    data = (
        _string("remote image 1")
        + _string("nivissvc.*")
        + _string("LV_ImageDTClassInfo")
        + _u32(1)
        + _string("PIXELS")
    )
    value, consumed, _ = _decode_element(data, lv_type)
    assert (value, consumed) == ("'remote image 1'", len(data))


def test_a_handle_refnum_is_still_four_bytes():
    lv_type = _prim("Refnum", ref_type="Queue")
    assert _decode_element(_u32(7), lv_type)[:2] == ("Refnum(7)", 4)


def _collection(name: str, element: LVType, value: LVType | None = None) -> LVType:
    return _prim(name, element_type=element, value_type=value)


def test_an_empty_set_and_map_are_just_a_count():
    string = _prim("String")
    assert _decode_element(_u32(0), _collection("Set", string)) == (
        "set()",
        4,
        [],
    )
    assert _decode_element(_u32(0), _collection("Map", string, string))[:2] == (
        "{}",
        4,
    )


def test_a_map_of_strings_to_string_lists():
    lv_type = _collection("Map", _prim("String"), _array(_prim("String")))
    data = _u32(1) + _string("PinGroup1") + _u32(2) + _string("Pin1") + _string("Pin2")
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data) == 37
    assert value == "{'PinGroup1': ['Pin1', 'Pin2']}"
    assert structured == [["'PinGroup1'", ["'Pin1'", "'Pin2'"]]]


def test_a_set_of_integers():
    data = _u32(2) + _u32(5) + _u32(9)
    value, consumed, _ = _decode_element(data, _collection("Set", _prim("NumUInt32")))
    assert (value, consumed) == ("{5, 9}", 12)


def _vctp(tmp_path: Path, descs: str) -> dict[int, LVType]:
    path = tmp_path / "vi.xml"
    path.write_text(f"<LVSR><VCTP><Section>{descs}</Section></VCTP></LVSR>")
    return parse_vctp_types(path)


def test_set_and_map_descriptors_name_their_member_types(tmp_path):
    types = _vctp(
        tmp_path,
        '<TypeDesc Type="String" /><TypeDesc Type="NumInt32" />'
        '<TypeDesc Type="115"><TypeDesc TypeID="0" /></TypeDesc>'
        '<TypeDesc Type="116"><TypeDesc TypeID="0" />'
        '<TypeDesc TypeID="1" /></TypeDesc>',
    )
    assert types[2].type_descriptor() == "set{String}"
    assert types[3].type_descriptor() == "map{String: I32}"


# === Waveforms, empty clusters, Void, repeated names ===


def test_an_analog_waveform_reads_every_part():
    data = (
        bytes(16)  # t0
        + struct.pack(">d", 1.0)  # dt
        + _u32(2)
        + struct.pack(">dd", 0.5, 1.5)  # Y
        + bytes([0])
        + _u32(0)
        + _u32(0)  # error: status, code, source
        + _EMPTY_VARIANT  # attributes
    )
    lv_type = _prim("MeasureData", measure_flavor="Float64Waveform")
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured["Y"] == ["0.5", "1.5"]
    assert structured["dt"] == "1.0"


def test_a_timestamp_shorter_than_16_bytes_is_unreadable():
    lv_type = _prim("MeasureData", measure_flavor="TimeStamp")
    assert _decode_element(bytes(8), lv_type) == (None, 0, None)


def test_an_empty_cluster_and_a_void_hold_no_bytes():
    empty = LVType(kind=LVTypeKind.CLUSTER, underlying_type="Cluster", fields=[])
    lv_type = _cluster(("a", _prim("NumUInt8")), ("e", empty), ("v", _prim("Void")))
    _, consumed, structured = _decode_element(bytes([5]), lv_type)
    assert consumed == 1
    assert structured == {"a": "5", "e": {}, "v": None}


def test_repeated_field_names_keep_every_value():
    assert unique_names(["Bottom", "Left", "Bottom", "Bottom"]) == [
        "Bottom",
        "Left",
        "Bottom (2)",
        "Bottom (3)",
    ]
    lv_type = _cluster(("x", _prim("NumUInt8")), ("x", _prim("NumUInt8")))
    value, _, structured = _decode_element(bytes([1, 2]), lv_type)
    assert structured == {"x": "1", "x (2)": "2"}
    assert value == "{'x': 1, 'x (2)': 2}"


def test_a_huge_array_is_read_to_its_end_without_keeping_its_values():
    count = MAX_STORED_ARRAY_ELEMENTS + 1
    data = _u32(count) + bytes(count)
    value, consumed, structured = _decode_element(data, _array(_prim("NumUInt8")))
    assert consumed == len(data)
    assert value == f"<{count} elements, values not kept>"
    assert structured == ValuesNotKept(count, (count,))


def test_a_huge_array_in_a_cluster_keeps_the_next_field():
    count = MAX_STORED_ARRAY_ELEMENTS + 1
    lv_type = _cluster(("frame", _array(_prim("NumUInt8"))), ("n", _prim("NumUInt8")))
    data = _u32(count) + bytes(count) + bytes([7])
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured["n"] == "7"
    assert find_unwritable(structured) == ValuesNotKept(count, (count,))
    assert "values not kept" in value


def test_code_generation_names_a_constant_whose_values_are_not_kept():
    count = MAX_STORED_ARRAY_ELEMENTS + 1
    lv_type = _array(_prim("NumUInt8"))
    const = ParsedConstant(
        uid="c", type_desc="", value=(_u32(count) + bytes(count)).hex()
    )
    decoded = decode_constant(const, lv_type=lv_type)
    assert decoded.unwritable == ValuesNotKept(count, (count,))
    node = Constant(
        id="c",
        value=decoded.value,
        lv_type=lv_type,
        unwritable=decoded.unwritable,
        label="Frame",
    )
    with pytest.raises(CodeGenError, match=rf"'Frame'.*{count} elements"):
        _format_constant(node)


def test_a_picture_with_drawing_is_shown_as_not_decoded_and_cannot_be_written():
    lv_type = _prim("Picture")
    data = _u32(3) + b"\x01\x02\x03"
    value, consumed, structured = _decode_element(data, lv_type)
    assert (value, consumed) == ("<Picture, 3 bytes of drawing not decoded>", 7)
    assert structured == UndecodedPicture(3)
    node = Constant(id="c", value=value, lv_type=lv_type, unwritable=structured)
    with pytest.raises(CodeGenError, match="Picture of 3 bytes"):
        _format_constant(node)


def test_an_empty_picture_is_written_as_none():
    lv_type = _prim("Picture")
    value, consumed, structured = _decode_element(_u32(0), lv_type)
    assert (consumed, structured) == (4, None)
    assert _format_constant(Constant(id="c", value=value, lv_type=lv_type)) == "None"


# === What a diagram constant stores ===


def test_an_all_zero_constant_has_one_extra_byte():
    assert constant_padding(bytes(5), 4) == 1
    assert constant_padding(bytes(4), 4) == 0
    assert constant_padding(bytes([0, 0, 0, 1]) + bytes(1), 4) == 0
    assert constant_padding(bytes(6), 4) == 0


# === The corpus ===

_PLUGINS = "ni-labview-icon-editor/resource/plugins"
_SESSIONS = "measurement-plugin-labview/Source/Runtime/Clients/Session Management V1"
_OPENG = "OpenG/extracted/File Group 0/examples/OpenG"
_EASYXML = "JKI-EasyXML/Source/Tests/EasyXMLTest"

# One or more VIs for every kind of value that once read short: multi-dimensional
# arrays, Pictures, class objects (default and with private data), tag refnums,
# Variants, extended-precision numbers, waveforms, Sets and Maps, repeated field
# names, Void/empty clusters, type definitions over arrays and refnums, and a
# front panel holding multi-megabyte values.
_AUDITED = [
    f"{_OPENG}/array/examples/Index Array Elements (2D) Example 01.vi",
    f"{_OPENG}/array/examples/Reverse 2D Array Example 01.vi",
    f"{_OPENG}/picture/Write GIF File Example.vi",
    "measurement-plugin-labview/Source/Example Measurements/Game Of Life/"
    "Game Of Life/Helpers/Next Generation.vi",
    "LabVIEW-OOP-Classes/DAQ/Analog Input/AI_class/utils/AI_New.vi",
    f"{_PLUGINS}/NIIconEditor/Miscellaneous/ni.com_iconlibrary/Install Glyphs.vi",
    f"{_PLUGINS}/NIIconEditor/Class/Ants/Initialization/Ants Initialization.vi",
    f"{_PLUGINS}/NIIconEditor/Controls/LayerList.ctl",
    "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon/Support/"
    "Extract Default Icon Data.vi",
    f"{_PLUGINS}/lv_icon.vi",
    f"{_SESSIONS}/Session Reservation/Create Session Reservation.vi",
    f"{_SESSIONS}/Session Reservation/Pins to Set.vi",
    f"{_SESSIONS}/Session Reservation/Reserve Registered Sessions.vi",
    f"{_SESSIONS}/Connection Map/Create.vi",
    "measurement-plugin-labview/Source/Runtime/Measurements/Measure Call Context/"
    "Create.vi",
    "measurement-plugin-labview/Source/Runtime/Clients/Discovery V1/"
    "Service Location FGV Impl.vi",
    "Event-Source-Actor/Source/Template Source/Event Source Actor/Pre Launch Init.vi",
    "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/Set Text Icon/Filter Words.vi",
    f"{_EASYXML}/testSupportedTypes/testNumericEXTPQ.vi",
    f"{_EASYXML}/testSupportedTypes/testRefnumVISA.vi",
    f"{_EASYXML}/testSupportedTypes/testRefnumDAQmx.vi",
    f"{_EASYXML}/testSupportedTypes/testVisaResource/VISA Resource Types.vi",
    f"{_EASYXML}/testUnsupportedTypes/testWaveform.vi",
    "JKI-EasyXML/Source/OpenG Derivative/Get Default Data from TD--WithTIMESTAMP.vi",
    "measurement-plugin-labview/Source/Runtime/Measurements/IMeasurement Service/"
    "Service Stop Handler.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/Class/Ants/Misc/"
    "RunningAnts.vi",
    "LabVIEW-DAQ/Intrinsic Optical Signal/Teledyne DALSA Pantera TF 1M60/"
    "MasterAcquisitionFile_DALSA_IOS.vi",
]


@pytest.mark.needs_samples
@pytest.mark.parametrize("relative", _AUDITED, ids=lambda p: Path(p).name)
def test_every_stored_value_is_read_to_its_exact_size(relative):
    vi = SAMPLES / relative
    if not vi.exists():
        pytest.skip(f"{relative} is not in this sample corpus")
    bd_xml, fp_xml, main_xml = resolve_extracted(vi)
    assert fp_xml is not None and main_xml is not None
    records = audit_vi(main_xml, bd_xml, fp_xml)
    wrong = [r for r in records if not r["exact"]]
    assert not wrong, [
        (r["kind"], r["size"], r["consumed"], r["cause"], r["tail"]) for r in wrong
    ]


# === A class instance's private data ===
# One cluster per level of the class hierarchy, oldest ancestor first (NI
# "Flattened Data", "Classes"); read with the private-data fields of the loaded
# class, else skipped by its stored byte count.


def _class_with(levels: list[list[ClusterField]] | None) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname="Lib.lvlib:Child.lvclass",
        class_file="Child.lvclass",
        class_levels=levels,
    )


def _name_section(*segments: str) -> bytes:
    body = b"".join(bytes([len(s)]) + s.encode() for s in segments) + b"\x00"
    section = bytes([len(body)]) + body
    return section + b"\x00" * (-len(section) % 4)


_VERSION = bytes([0, 1, 0, 0, 0, 0, 0, 0])
_BASE = [ClusterField(name="count", type=_prim("NumUInt8"))]
_OWN = [
    ClusterField(name="label", type=_prim("String")),
    ClusterField(name="mode", type=_prim("UnitUInt16")),
]


def _two_level_object() -> bytes:
    base = bytes([7])
    own = _string("hi") + (3).to_bytes(2, "big")
    return (
        _u32(2)
        + _name_section("Base.lvclass", "Child.lvclass")
        + _VERSION * 2
        + _u32(len(base))
        + base
        + _u32(len(own))
        + own
    )


def test_a_loaded_class_shows_its_private_data_after_its_name():
    data = _two_level_object() + bytes([9])
    lv_type = _cluster(
        ("obj", _class_with([_BASE, _OWN])), ("after", _prim("NumUInt8"))
    )
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert value == (
        "{'obj': Child.lvclass{'count': 7, 'label': 'hi', 'mode': 3}, 'after': 9}"
    )
    assert structured == {"obj": "Child.lvclass", "after": "9"}


def test_a_class_that_is_not_loaded_keeps_its_extent_and_says_its_data_is_unread():
    data = _two_level_object() + bytes([9])
    lv_type = _cluster(("obj", _class_with(None)), ("after", _prim("NumUInt8")))
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert value == "{'obj': Child.lvclass(private data not read), 'after': 9}"
    assert structured["obj"] == UnreadPrivateData("Child.lvclass")
    assert find_unwritable(structured) == UnreadPrivateData("Child.lvclass")


def test_private_data_the_loaded_fields_do_not_read_exactly_is_skipped_and_said_so():
    data = _two_level_object()
    wrong = [[ClusterField(name="count", type=_prim("NumUInt32"))], _OWN]
    unread = "Child.lvclass(private data not read)"
    assert _decode_element(data, _class_with(wrong))[:2] == (unread, len(data))
    # A hierarchy of a different depth than the stored object.
    assert _decode_element(data, _class_with([_OWN]))[:2] == (unread, len(data))


def test_a_default_object_has_no_private_data_to_show():
    data = _u32(1) + _name_section("Child.lvclass") + bytes(8)
    assert _decode_element(data, _class_with([_OWN]))[:2] == (
        "Child.lvclass",
        len(data),
    )


def _class_graph():
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.node_kinds import NodeType

    g = InMemoryVIGraph()
    g._dep_graph.add_node(
        "Base.lvclass",
        node_type=NodeType.CLASS,
        fields=_BASE,
        parent_class="LabVIEW Object",
    )
    g._dep_graph.add_node(
        "Child.lvclass",
        node_type=NodeType.CLASS,
        fields=_OWN,
        parent_class="Base",
        parent_key="Base.lvclass",
    )
    return g


def test_the_graph_gives_a_class_its_levels_oldest_ancestor_first():
    g = _class_graph()
    levels = g.get_class_private_levels("Child.lvclass")
    assert levels is not None
    assert [[f.name for f in level] for level in levels] == [
        ["count"],
        ["label", "mode"],
    ]
    assert g.get_class_private_levels("Missing.lvclass") is None


def test_a_type_holding_a_class_is_stamped_with_a_copy():
    g = _class_graph()
    plain = LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname="Child.lvclass",
    )
    holder = _cluster(("objects", _array(plain)))
    stamped = g.with_class_levels(holder)
    assert stamped is not holder
    assert holder.fields[0].type.element_type.class_levels is None
    element = stamped.fields[0].type.element_type
    assert [len(level) for level in element.class_levels] == [1, 2]
    unrelated = _cluster(("n", _prim("NumUInt8")))
    assert g.with_class_levels(unrelated) is unrelated


@pytest.mark.needs_samples
def test_corpus_a_loaded_class_shows_the_private_data_of_its_default():
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.loading import LoadMode

    root = SAMPLES / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon"
    vi = root / "Support/Extract Default Icon Data.vi"

    def default_of(search_paths):
        g = InMemoryVIGraph()
        g.load_vi(vi, mode=LoadMode.FULL, search_paths=search_paths)
        node = g._graph.nodes[str(vi.resolve())]["node"]
        return next(t.default_value for t in node.terminals if "Icon Data" in t.name)

    loaded = default_of(None)
    assert "'All User Layers': [Layer.lvclass{'Origin': {'Horizontal': 0, " in loaded
    assert (
        "'LayerName': 'VI Icon', 'Opacity': 100, 'Visibility?': True}, 'Type': 2}]"
        in loaded
    )


def _instance(name: str) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname=name,
    )


def _object(name: str, private: bytes | None) -> bytes:
    """A one-level class object: the default object (no private data) or one that
    stores ``private``."""
    if private is None:
        return _u32(1) + _name_section(name) + bytes(8)
    return _u32(1) + _name_section(name) + _VERSION + _u32(len(private)) + private


def _graph_of(classes: dict[str, list[ClusterField]]):
    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.node_kinds import NodeType

    g = InMemoryVIGraph()
    for name, fields in classes.items():
        g._dep_graph.add_node(
            name,
            node_type=NodeType.CLASS,
            fields=fields,
            parent_class="LabVIEW Object",
        )
    return g


def test_a_class_held_in_another_classes_private_data_reads_its_own():
    g = _graph_of(
        {
            "Outer.lvclass": [
                ClusterField(name="inner", type=_instance("Inner.lvclass")),
                ClusterField(name="n", type=_prim("NumUInt8")),
            ],
            "Inner.lvclass": [ClusterField(name="v", type=_prim("NumUInt8"))],
        }
    )
    stamped = g.with_class_levels(_instance("Outer.lvclass"))
    inner_type = stamped.class_levels[0][0].type
    assert [f.name for f in inner_type.class_levels[0]] == ["v"]
    data = _object("Outer.lvclass", _object("Inner.lvclass", bytes([4])) + bytes([8]))
    value, consumed, _ = _decode_element(data, stamped)
    assert consumed == len(data)
    assert value == "Outer.lvclass{'inner': Inner.lvclass{'v': 4}, 'n': 8}"


@pytest.mark.parametrize(
    "classes",
    [
        {"Node.lvclass": ("next", "Node.lvclass")},
        {"A.lvclass": ("b", "B.lvclass"), "B.lvclass": ("a", "A.lvclass")},
    ],
    ids=["itself", "through another class"],
)
def test_a_class_that_holds_itself_stops_at_the_inner_instance(classes):
    g = _graph_of(
        {
            name: [
                ClusterField(name=field, type=_instance(target)),
                ClusterField(name="v", type=_prim("NumUInt8")),
            ]
            for name, (field, target) in classes.items()
        }
    )
    first = next(iter(classes))
    stamped = g.with_class_levels(_instance(first))
    assert stamped.class_levels is not None
    # Walk down the stamped fields: the chain ends where a class repeats.
    seen, current = [first], stamped
    while current.class_levels is not None:
        current = current.class_levels[0][0].type
        seen.append(current.classname)
    assert len(seen) == len(classes) + 1
    assert current.classname == first
    # The innermost instance reads as its name with its exact extent.
    name = first.rsplit(".", 1)[0]
    inner = _object(first, None)
    if len(classes) == 1:
        data = _object(first, inner + bytes([5]))
        shown = f"{name}.lvclass{{'next': {name}.lvclass, 'v': 5}}"
    else:
        other = next(c for c in classes if c != first)
        inside = _object(other, _object(first, None) + bytes([6]))
        data = _object(first, inside + bytes([5]))
        shown = f"{first}{{'b': {other}{{'a': {first}, 'v': 6}}, 'v': 5}}"
    value, consumed, _ = _decode_element(data, stamped)
    assert consumed == len(data)
    assert value == shown


# === Large arrays go to a data file beside the generated module ===


def _large_array_constant(
    monkeypatch, element: str, dims: tuple[int, ...], blob: bytes
):
    monkeypatch.setattr("lvkit.parser.vi.MAX_STORED_ARRAY_ELEMENTS", 3)
    lv_type = _array(_prim(element), len(dims))
    data = b"".join(_u32(d) for d in dims) + blob
    const = ParsedConstant(uid="c", type_desc="", value=data.hex())
    decoded = decode_constant(const, lv_type=lv_type)
    node = Constant(
        id="vi::77",
        value=decoded.value,
        lv_type=lv_type,
        unwritable=decoded.unwritable,
        label="Frame",
    )
    return node, decoded


def test_a_large_array_keeps_its_stored_bytes_and_still_displays_in_words(
    monkeypatch,
):
    node, decoded = _large_array_constant(
        monkeypatch, "NumUInt16", (2, 3), bytes(range(12))
    )
    assert decoded.value == "<6 elements, values not kept>"
    assert decoded.unwritable.element_bytes == bytes(range(12))
    assert decoded.unwritable.dimension_sizes == (2, 3)


@pytest.mark.parametrize(
    ("element", "blob", "expected"),
    [
        (
            "NumUInt16",
            bytes([0, 1, 0, 2, 0, 3, 1, 0, 2, 0, 255, 255]),
            [[1, 2, 3], [256, 512, 65535]],
        ),
        ("NumInt8", bytes([1, 255, 2, 254, 3, 253]), [[1, -1, 2], [-2, 3, -3]]),
        (
            "NumFloat32",
            struct.pack(">6f", 1, 2, 3, 4, 5, 6.5),
            [[1, 2, 3], [4, 5, 6.5]],
        ),
        (
            "Boolean",
            bytes([1, 0, 1, 0, 0, 1]),
            [[True, False, True], [False, False, True]],
        ),
    ],
)
def test_generated_code_loads_a_large_array_from_its_data_file(
    monkeypatch, tmp_path, element, blob, expected
):
    from lvkit.codegen.context import CodeGenContext

    node, _ = _large_array_constant(monkeypatch, element, (2, 3), blob)
    ctx = CodeGenContext(data_files={}, module_stem="my_vi")
    expression = ctx.constant_expression(node)
    assert "from lvkit.runtime import lv as _lv" in ctx.imports
    (file_name, content), = ctx.data_files.items()
    assert file_name == "my_vi.frame_77.bin"
    assert content == blob

    module = tmp_path / "my_vi.py"
    module.write_text(
        "from lvkit.runtime import lv as _lv\n" f"value = {expression}\n"
    )
    (tmp_path / file_name).write_bytes(content)
    namespace: dict = {"__file__": str(module)}
    exec(compile(module.read_text(), str(module), "exec"), namespace)  # noqa: S102
    assert namespace["value"] == expected


def test_a_large_one_dimensional_array_loads_flat(monkeypatch, tmp_path):
    from lvkit.runtime import lv

    (tmp_path / "a.bin").write_bytes(struct.pack(">4I", 7, 8, 9, 4000000000))
    module = tmp_path / "m.py"
    assert lv.load_array(str(module), "a.bin", "I", (4,)) == [7, 8, 9, 4000000000]


def test_a_large_array_without_a_place_for_its_file_says_so(monkeypatch):
    from lvkit.codegen.context import CodeGenContext

    node, _ = _large_array_constant(monkeypatch, "NumUInt8", (6,), bytes(6))
    with pytest.raises(CodeGenError, match="returns only the module's text"):
        CodeGenContext().constant_expression(node)


def test_a_large_array_inside_a_cluster_constant_is_named_in_the_error(monkeypatch):
    from lvkit.codegen.context import CodeGenContext

    monkeypatch.setattr("lvkit.parser.vi.MAX_STORED_ARRAY_ELEMENTS", 3)
    lv_type = _cluster(("frame", _array(_prim("NumUInt8"))), ("n", _prim("NumUInt8")))
    data = _u32(6) + bytes(6) + bytes([7])
    decoded = decode_constant(
        ParsedConstant(uid="c", type_desc="", value=data.hex()), lv_type=lv_type
    )
    node = Constant(
        id="vi::5",
        value=decoded.value,
        lv_type=lv_type,
        unwritable=decoded.unwritable,
        label="Settings",
    )
    with pytest.raises(CodeGenError, match="'Settings'.*6 elements"):
        CodeGenContext(data_files={}).constant_expression(node)

"""Multi-dimensional array values, and a TypeDef over an Array or Refnum.

A flattened array stores one 32-bit size per dimension (slowest-varying first),
then the elements in row-major order -- NI's "Flattened Data" page, "Arrays":
https://docs-be.ni.com/api/bundle/labview/page/flattened-data.html
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.codegen.context import _format_constant
from lvkit.extractor import resolve_extracted
from lvkit.graph.construction import decode_constant
from lvkit.graph.models import Constant
from lvkit.models import ClusterField, LVType, LVTypeKind, leading_element
from lvkit.parser.models import ParsedConstant
from lvkit.parser.type_mapping import parse_vctp_types
from lvkit.parser.utils import decode_xml_entities_to_bytes, strip_surrounding_quotes
from lvkit.parser.vi import _decode_element, parse_vi
from lvkit.render.nodes import _array_const_values, _field_summary_value

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"


def _u32(*values: int) -> bytes:
    return b"".join(v.to_bytes(4, "big") for v in values)


def _prim(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _array(element: LVType, dimensions: int = 1) -> LVType:
    return LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=element,
        dimensions=dimensions,
    )


def _cluster(**fields: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(name=n, type=t) for n, t in fields.items()],
    )


# === Decoding ===


def test_one_dimension_is_unchanged():
    data = _u32(3, 7, 8, 9)
    assert _decode_element(data, _array(_prim("NumUInt32"))) == (
        "[7, 8, 9]",
        16,
        ["7", "8", "9"],
    )


def test_two_dimensions_nest_rows_and_consume_every_byte():
    data = _u32(2, 3, 1, 2, 3, 4, 5, 6)
    value, consumed, structured = _decode_element(data, _array(_prim("NumUInt32"), 2))
    assert value == "[[1, 2, 3], [4, 5, 6]]"
    assert consumed == len(data)
    assert structured == [["1", "2", "3"], ["4", "5", "6"]]


def test_three_dimensions_nest_slowest_first():
    data = _u32(2, 2, 2, 1, 2, 3, 4, 5, 6, 7, 8)
    value, consumed, structured = _decode_element(data, _array(_prim("NumUInt32"), 3))
    assert value == "[[[1, 2], [3, 4]], [[5, 6], [7, 8]]]"
    assert consumed == len(data)
    assert structured[1][0] == ["5", "6"]


def test_boolean_grid():
    data = _u32(2, 2) + bytes([1, 0, 0, 1])
    value, consumed, _ = _decode_element(data, _array(_prim("Boolean"), 2))
    assert (value, consumed) == ("[[True, False], [False, True]]", 12)


def test_empty_two_dimensional_array():
    data = _u32(0, 0)
    assert _decode_element(data, _array(_prim("NumUInt32"), 2)) == ("[]", 8, [])


def test_rows_of_zero_columns_keep_their_rows():
    data = _u32(3, 0)
    assert _decode_element(data, _array(_prim("NumUInt32"), 2))[0] == "[[], [], []]"


def test_a_header_shorter_than_the_dimension_count_is_undecodable():
    assert _decode_element(_u32(2), _array(_prim("NumUInt32"), 2))[0] is None


def test_string_table():
    cell = lambda s: _u32(len(s)) + s.encode()  # noqa: E731
    data = _u32(2, 2) + cell("a") + cell("bc") + cell("") + cell("d")
    value, consumed, _ = _decode_element(data, _array(_prim("String"), 2))
    assert value == "[['a', 'bc'], ['', 'd']]"
    assert consumed == len(data)


def test_a_two_dimensional_field_keeps_the_following_fields_aligned():
    lv_type = _cluster(
        grid=_array(_prim("NumUInt8"), 2),
        after=_prim("NumUInt8"),
        names=_array(_prim("String")),
    )
    data = _u32(2, 2) + bytes([1, 2, 3, 4]) + bytes([9]) + _u32(1, 2) + b"ab"
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured == {
        "grid": [["1", "2"], ["3", "4"]],
        "after": "9",
        "names": ["'ab'"],
    }
    assert value == "{'grid': [[1, 2], [3, 4]], 'after': 9, 'names': ['ab']}"


def test_an_empty_two_dimensional_field_keeps_the_following_fields_aligned():
    lv_type = _cluster(grid=_array(_prim("NumFloat64"), 2), n=_prim("NumUInt8"))
    data = _u32(0, 0) + bytes([5])
    _, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured == {"grid": [], "n": "5"}


def test_a_picture_is_read_past_so_the_next_field_stays_aligned():
    lv_type = _cluster(pic=_prim("Picture"), name=_prim("String"))
    data = _u32(3) + b"\x01\x02\x03" + _u32(2) + b"hi"
    _, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured == {"pic": None, "name": "'hi'"}


# === Constants and generated Python ===


def test_a_two_dimensional_constant_generates_a_list_of_lists():
    lv_type = _array(_prim("NumUInt32"), 2)
    const = ParsedConstant(uid="c", type_desc="", value=(_u32(2, 2, 1, 2, 3, 4)).hex())
    _, value, _ = decode_constant(const, lv_type=lv_type)
    assert value == "[[1, 2], [3, 4]]"
    expr = _format_constant(Constant(id="c", value=value, lv_type=lv_type))
    assert eval(expr) == [[1, 2], [3, 4]]


def test_a_two_dimensional_constant_in_a_cluster_keeps_its_neighbours():
    lv_type = _cluster(grid=_array(_prim("NumUInt8"), 2), n=_prim("NumUInt8"))
    data = _u32(1, 2) + bytes([7, 8, 9])
    const = ParsedConstant(uid="c", type_desc="", value=data.hex())
    _, value, _ = decode_constant(const, lv_type=lv_type)
    assert "'n': 9" in value
    assert "[[7, 8]]" in value


# === Consumers of the nested value ===


def test_leading_element_walks_every_dimension():
    assert leading_element([[["1", "2"], ["3"]], []]) == "1"
    assert leading_element([]) is None
    assert leading_element([[]]) is None
    assert leading_element("5") == "5"
    assert leading_element({"a": 1}) == {"a": 1}


def test_a_multi_dimensional_constant_glyph_has_one_cell_per_first_dimension_entry():
    assert _array_const_values("[[1, 2], [3, 4]]") == [1, 3]
    assert _array_const_values("[1, 2]") == [1, 2]
    assert _array_const_values("[]") == []


def test_the_hover_summary_counts_every_element():
    lv_type = _array(_prim("String"), 2)
    rows = [["a", "b", "c"], ["d", "e", "f"]]
    assert _field_summary_value(lv_type, rows) == "[6 elements]"
    assert _field_summary_value(lv_type, [["a"]]) == "[1 element]"


# === A TypeDef over an Array or a Refnum ===

_VCTP = """<LVSR><VCTP>
<Section>{descs}</Section>
</VCTP></LVSR>"""


def _types(tmp_path: Path, descs: str) -> dict[int, LVType]:
    path = tmp_path / "vi.xml"
    path.write_text(_VCTP.format(descs=descs))
    return parse_vctp_types(path)


def test_a_typedef_over_an_array_keeps_its_element_and_dimensions(tmp_path):
    types = _types(
        tmp_path,
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="Array" Nested="True" Label="Type Descriptor">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        '<TypeDesc TypeID="1" /></TypeDesc>'
        '<Label Text="Type Descriptor__ogtk.ctl" /></TypeDesc>'
        '<TypeDesc Type="NumInt16" />',
    )
    lv_type = types[0]
    assert lv_type.kind == LVTypeKind.ARRAY
    assert lv_type.typedef_name == "Type Descriptor__ogtk.ctl"
    assert lv_type.dimensions == 1
    assert lv_type.element_type is not None
    assert lv_type.element_type.underlying_type == "NumInt16"
    assert lv_type.type_descriptor() == "[I16]"


def test_a_typedef_over_a_two_dimensional_array_decodes_its_value(tmp_path):
    types = _types(
        tmp_path,
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="Array" Nested="True">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        '<TypeDesc TypeID="1" /></TypeDesc>'
        '<Label Text="Grid.ctl" /></TypeDesc>'
        '<TypeDesc Type="NumUInt8" />',
    )
    assert types[0].dimensions == 2
    value, consumed, _ = _decode_element(_u32(1, 2) + bytes([4, 5]), types[0])
    assert (value, consumed) == ("[[4, 5]]", 10)


def test_a_typedef_over_a_user_event_keeps_its_payload_and_name(tmp_path):
    types = _types(
        tmp_path,
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="Refnum" Nested="True" RefType="UserEvent">'
        '<TypeDesc TypeID="1" /></TypeDesc>'
        '<Label Text="Stop Server User Event.ctl" /></TypeDesc>'
        '<TypeDesc Type="Boolean" Label="Stop Server" />',
    )
    lv_type = types[0]
    assert lv_type.typedef_name == "Stop Server User Event.ctl"
    assert lv_type.ref_type == "UserEvent"
    assert lv_type.user_event_name == "Stop Server"
    assert lv_type.element_type is not None
    assert lv_type.element_type.underlying_type == "Boolean"


def test_a_typedef_over_a_class_refnum_keeps_the_class(tmp_path):
    types = _types(
        tmp_path,
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="Refnum" Nested="True" RefType="UDClassInst">'
        '<Item Text="Lib.lvlib" /><Item Text="Cls.lvclass" /></TypeDesc>'
        '<Label Text="Cls Ref.ctl" /></TypeDesc>',
    )
    assert types[0].classname == "Lib.lvlib:Cls.lvclass"
    assert types[0].typedef_name == "Cls Ref.ctl"


def test_typedef_clusters_and_enums_keep_their_fields_and_labels(tmp_path):
    types = _types(
        tmp_path,
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="Cluster" Nested="True">'
        '<TypeDesc TypeID="2" /></TypeDesc>'
        '<Label Text="Pair.ctl" /></TypeDesc>'
        '<TypeDesc Type="TypeDef">'
        '<TypeDesc Type="UnitUInt16" Nested="True">'
        "<EnumLabel>Off</EnumLabel><EnumLabel>On</EnumLabel></TypeDesc>"
        '<Label Text="Mode.ctl" /></TypeDesc>'
        '<TypeDesc Type="NumInt32" Label="Count" />',
    )
    cluster, enum = types[0], types[1]
    assert cluster.kind == LVTypeKind.CLUSTER
    assert cluster.typedef_name == "Pair.ctl"
    assert cluster.fields is not None
    assert [f.name for f in cluster.fields] == ["Count"]
    assert enum.kind == LVTypeKind.ENUM
    assert enum.typedef_name == "Mode.ctl"
    assert enum.values is not None
    assert list(enum.values) == ["Off", "On"]


# === Real VIs from the sample corpus ===


def _control(vi: str, name: str):
    parsed = parse_vi(SAMPLES / vi)
    return next(c for c in parsed.front_panel.controls if c.name == name)


_OPENG_ARRAY = "OpenG/extracted/File Group 0/examples/OpenG/array/examples"


@pytest.mark.needs_samples
def test_corpus_two_dimensional_default_is_three_rows_of_four():
    ctrl = _control(f"{_OPENG_ARRAY}/Index Array Elements (2D) Example 01.vi", "array")
    assert ctrl.default_value == (
        "[[16716306, 1250047, 65309, 16777215], [0, 5898232, 946944, 16716521], "
        "[12961221, 16775680, 10556159, 16758850]]"
    )


@pytest.mark.needs_samples
def test_corpus_boolean_grid_is_four_rows_of_six():
    ctrl = _control(f"{_OPENG_ARRAY}/Reverse 2D Array Example 01.vi", "Array")
    assert ctrl.default_value is not None
    assert ctrl.default_value.startswith("[[True, False, False, False, True, False]")
    assert ctrl.default_value.count("], [") == 3


@pytest.mark.needs_samples
def test_corpus_cluster_fields_after_an_empty_grid_are_aligned():
    ctrl = _control(
        "LabVIEW-OOP-Classes/DAQ/Analog Input/AI_class/utils/AI_New.vi", "Data"
    )
    assert ctrl.default_value is not None
    assert "'AI Readings': []" in ctrl.default_value
    assert "'AI Module Names (NI-Max Name)': ['DO-Sink-32CH']" in ctrl.default_value
    assert "'Number of Channels Per AI  Module': [32]" in ctrl.default_value


@pytest.mark.needs_samples
def test_corpus_typedef_array_control_shows_its_element_type_and_default():
    ctrl = _control(
        "ni-labview-icon-editor/resource/plugins/NIIconEditor/Miscellaneous/"
        "ni.com_iconlibrary/Install Glyphs.vi",
        "Glyph list",
    )
    assert ctrl.lv_type is not None
    assert ctrl.lv_type.type_descriptor() != "[?]"
    assert ctrl.default_value == (
        "[{'Install': False, 'Current': None, 'Icon Library': None, "
        "'Subfolder': '', 'Name': ''}]"
    )


@pytest.mark.needs_samples
def test_corpus_typedef_user_event_control_names_its_payload():
    ctrl = _control(
        "measurement-plugin-labview/Source/Runtime/Measurements/"
        "IMeasurement Service/Service Stop Handler.vi",
        "stop server user event",
    )
    assert ctrl.lv_type is not None
    assert ctrl.lv_type.ref_type == "UserEvent"
    assert ctrl.lv_type.user_event_name == "Stop Server"


_ICON_PLUGINS = "ni-labview-icon-editor/resource/plugins"


def _default_blobs(vi: str) -> list[bytes]:
    """Every non-empty front-panel ``DefaultData`` blob of ``vi``, as stored."""
    _bd, fp, _main = resolve_extracted(SAMPLES / vi)
    return [
        blob
        for elem in ET.parse(fp).getroot().iter("DefaultData")
        if (
            blob := decode_xml_entities_to_bytes(
                strip_surrounding_quotes(elem.text or "")
            )
        )
    ]


def _assert_whole_blob_decodes(vi: str, control: str, size: int) -> None:
    ctrl = _control(vi, control)
    assert ctrl.lv_type is not None
    blob = next(b for b in _default_blobs(vi) if len(b) == size)
    value, consumed, _ = _decode_element(blob, ctrl.lv_type)
    assert value is not None
    assert consumed == size


@pytest.mark.needs_samples
def test_corpus_four_pictures_in_a_cluster_consume_the_whole_default():
    """`User layers` holds four non-empty Pictures among other fields; every
    byte of its 12,452-byte default is accounted for."""
    _assert_whole_blob_decodes(
        f"{_ICON_PLUGINS}/NIIconEditor/Controls/LayerList.ctl", "User layers", 12452
    )


@pytest.mark.needs_samples
def test_corpus_pictures_in_a_cluster_of_clusters_consume_the_whole_default():
    _assert_whole_blob_decodes(
        f"{_ICON_PLUGINS}/lv_icon.vi", "Framework Templates", 1172
    )


# === A LabVIEW class object is read past in full ===
# NI "Flattened Data", "Classes": level in hierarchy (4 bytes; 0 = LabVIEW
# Object, nothing follows), the class name (a length byte, Pascal strings, a zero
# length byte, padded to 4), one 8-byte version per level (one level at version
# 0 is the default data, nothing follows), then per level a 4-byte byte count and
# that many bytes of private data, padded to 4.


def _class_type(name: str = "Lib.lvlib:Layer.lvclass") -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type="UDClassInst",
        classname=name,
    )


def _name_section(*segments: str) -> bytes:
    body = b"".join(bytes([len(s)]) + s.encode() for s in segments) + b"\x00"
    section = bytes([len(body)]) + body
    return section + b"\x00" * (-len(section) % 4)


def test_a_level_zero_object_is_four_bytes():
    assert _decode_element(_u32(0), _class_type()) == (
        "Layer.lvclass",
        4,
        "Layer.lvclass",
    )


def test_a_default_object_has_no_private_data():
    data = _u32(1) + _name_section("Lib.lvlib", "Layer.lvclass") + b"\x00" * 8
    assert _decode_element(data, _class_type())[1] == len(data)


def test_private_data_is_skipped_and_padded_to_four():
    version = bytes([0, 1, 0, 0, 0, 0, 0, 0])
    data = (
        _u32(1)
        + _name_section("Layer.lvclass")
        + version
        + _u32(5)
        + b"abcde\x00\x00\x00"
    )
    assert _decode_element(data, _class_type())[1] == len(data)


def test_every_level_carries_its_own_version_and_private_data():
    versions = bytes([0, 1, 0, 0, 0, 0, 0, 0]) * 2
    data = (
        _u32(2)
        + _name_section("Child.lvclass")
        + versions
        + _u32(0)
        + _u32(3)
        + b"xyz\x00"
    )
    assert _decode_element(data, _class_type())[1] == len(data)


def test_fields_after_a_class_object_stay_aligned():
    lv_type = _cluster(
        layers=_array(_class_type()), after=_prim("NumUInt8"), tail=_prim("NumUInt8")
    )
    obj = (
        _u32(1)
        + _name_section("Layer.lvclass")
        + bytes([0, 1])
        + b"\x00" * 6
        + _u32(2)
        + b"ab\x00\x00"
    )
    data = _u32(2) + obj + obj + bytes([7, 8])
    value, consumed, structured = _decode_element(data, lv_type)
    assert consumed == len(data)
    assert structured == {
        "layers": ["Layer.lvclass", "Layer.lvclass"],
        "after": "7",
        "tail": "8",
    }


@pytest.mark.needs_samples
def test_corpus_class_objects_in_clusters_consume_the_whole_default():
    """Eight class objects among other fields: all 3,592 stored bytes."""
    vi = (
        f"{_ICON_PLUGINS}/NIIconEditor/Class/Ants/Initialization/Ants Initialization.vi"
    )
    blobs = {len(b): b for b in _default_blobs(vi)}
    parsed = parse_vi(SAMPLES / vi)
    assert any(
        (result := _decode_element(blobs[3592], c.lv_type))[0] is not None
        and result[1] == 3592
        for c in parsed.front_panel.controls
        if c.lv_type is not None
    )


@pytest.mark.needs_samples
def test_corpus_fields_after_an_array_of_class_objects_are_aligned():
    vi = (
        "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon/Support/"
        "Extract Default Icon Data.vi"
    )
    controls = parse_vi(SAMPLES / vi).front_panel.controls
    lv_type = next(
        c.lv_type
        for c in controls
        if c.lv_type is not None
        and c.lv_type.type_descriptor().startswith("LabVIEW Icon Stored Information")
    )
    blob = next(b for b in _default_blobs(vi) if len(b) == 181)
    _, _, structured = _decode_element(blob, lv_type)
    assert isinstance(structured, dict)
    assert structured["All User Layers"] == ["Layer.lvclass"]
    body = structured["Body Text"]
    assert [body[f"Line {n} text"] for n in range(1, 5)] == ["''"] * 4
    assert [body[f"Line {n} color"] for n in range(1, 5)] == ["0"] * 4

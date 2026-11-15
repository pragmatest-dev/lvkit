"""A cluster element records whether the VI gives it a label. An element
without one is named by its position (``field_<n>``); an element whose real label
is ``field_0`` is labelled."""

from __future__ import annotations

import struct
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pylabview.LVdatatype  # noqa: F401 -- imported first: pylabview's modules are circular

from lvkit.codegen.plain_values import _is_unlabelled
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser import td_pool
from lvkit.parser.extfunc import _prototype_names, _row_name
from lvkit.parser.flat_type import FlatTD, descriptor_type
from lvkit.parser.fp_heap_type import reconstruct_control_lvtype
from lvkit.parser.type_mapping import parse_vctp_types
from lvkit.structure import _resolve_type_ids, private_data_field_to_cluster_field

INT32 = 0x03
CLUSTER = 0x50


def _flags(fields: list[ClusterField] | None) -> list[tuple[str, bool]]:
    assert fields is not None
    return [(f.name, f.labelled) for f in fields]


def _pstr(text: str) -> bytes:
    return bytes([len(text)]) + text.encode("latin1")


def _td(code: int, body: bytes, flags: int = 0x40) -> bytes:
    return struct.pack(">HBB", 4 + len(body), flags, code) + body


def test_td_pool_cluster_records_labels() -> None:
    members = (
        _td(INT32, _pstr("a"))
        + _td(INT32, b"", flags=0)
        + _td(INT32, _pstr("field_0"))
    )
    cluster = _td(CLUSTER, struct.pack(">HHHH", 3, 0, 1, 2), flags=0)
    pool = td_pool.TdPool(
        td_pool.read_td_list(struct.pack(">I", 4) + members + cluster).tds
    )
    assert pool.failures == {}
    assert _flags(pool.lvtypes[3].fields) == [
        ("a", True),
        ("field_1", False),
        ("field_0", True),
    ]


def test_flat_type_cluster_records_labels() -> None:
    descriptors = [
        FlatTD(INT32, b"", "a"),
        FlatTD(INT32, b"", None),
        FlatTD(INT32, b"", "field_0"),
        FlatTD(CLUSTER, struct.pack(">HHHH", 3, 0, 1, 2), None),
    ]
    lv_type = descriptor_type(descriptors, 3)
    assert lv_type is not None
    assert _flags(lv_type.fields) == [
        ("a", True),
        ("field_1", False),
        ("field_0", True),
    ]


def test_vctp_cluster_records_labels(tmp_path: Path) -> None:
    xml_path = tmp_path / "labels.xml"
    xml_path.write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <RSRC><VCTP><Section>
                <TypeDesc Type="NumInt32" Label="a" />
                <TypeDesc Type="NumInt32" />
                <TypeDesc Type="NumInt32" Label="field_0" />
                <TypeDesc Type="Cluster">
                    <TypeDesc TypeID="0" />
                    <TypeDesc TypeID="1" />
                    <TypeDesc TypeID="2" />
                </TypeDesc>
            </Section></VCTP></RSRC>
        """)
    )
    assert _flags(parse_vctp_types(xml_path)[3].fields) == [
        ("a", True),
        ("field_1", False),
        ("field_0", True),
    ]


def test_front_panel_cluster_elements_are_unlabelled() -> None:
    clust = ET.fromstring(
        '<ddo class="stdClust" uid="1">'
        '  <ddoList elements="2">'
        '    <SL__arrayElement uid="10" />'
        '    <SL__arrayElement uid="20" />'
        "  </ddoList>"
        '  <paneHierarchy class="pane">'
        "    <zPlaneList>"
        '      <SL__arrayElement class="stdString" uid="10" index="0" />'
        '      <SL__arrayElement class="stdNum" uid="20" index="1" />'
        "    </zPlaneList>"
        "  </paneHierarchy>"
        "</ddo>"
    )
    lv_type = reconstruct_control_lvtype(clust)
    assert lv_type is not None
    assert _flags(lv_type.fields) == [("field_0", False), ("field_1", False)]


def test_class_private_data_records_labels() -> None:
    root = ET.fromstring(
        textwrap.dedent("""\
            <RSRC><VCTP><Section>
                <TypeDesc Type="String" Label="a" />
                <TypeDesc Type="String" />
                <TypeDesc Type="String" Label="field_0" />
            </Section></VCTP></RSRC>
        """)
    )
    fields = _resolve_type_ids(root, ["0", "1", "2"])
    assert [(f.name, f.labelled) for f in fields] == [
        ("a", True),
        ("field_1", False),
        ("field_0", True),
    ]
    converted = [private_data_field_to_cluster_field(f) for f in fields]
    assert _flags(converted) == [("a", True), ("field_1", False), ("field_0", True)]


def test_extfunc_prototype_name_is_nameless_only_without_a_label() -> None:
    elem = ET.fromstring("<xml><funcTD>0</funcTD></xml>")
    prototype = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="field_0", labelled=False),
            ClusterField(name="field_1", labelled=True),
        ],
    )
    names = _prototype_names(elem, {0: prototype})
    assert names == ["", "field_1"]
    assert _row_name(None, names, 0, True) == "return value"
    assert _row_name(None, names, 0, False) is None
    assert _row_name(None, names, 1, False) == "field_1"


def test_is_unlabelled_reads_the_flag_not_the_name() -> None:
    assert _is_unlabelled([ClusterField("field_0", None, labelled=False)])
    assert not _is_unlabelled([ClusterField("field_0", None, labelled=True)])
    assert not _is_unlabelled(
        [ClusterField("field_0", None, False), ClusterField("x", None, True)]
    )

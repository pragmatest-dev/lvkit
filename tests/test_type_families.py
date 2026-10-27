"""Type families (the same wire type stored differently) and the refnum layouts
the type pool reads, checked against pylabview's own readers."""

from __future__ import annotations

import ast
import struct

import pylabview.LVdatatype  # noqa: F401 -- imported first: pylabview's modules are circular
import pylabview.LVdatatyperef as lv_ref
import pytest

from lvkit.codegen.ast_utils import default_value_expr
from lvkit.lv_type_codes import (
    REFNUM_TYPE,
    is_boolean_type_name,
    is_integer_type_name,
    is_string_type_name,
    plain_number_name,
)
from lvkit.models import LVType, LVTypeKind
from lvkit.parser import td_pool
from lvkit.primitive_resolver import get_resolver


def _prim(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


@pytest.mark.parametrize("name", ["Boolean", "BooleanU16"])
def test_every_boolean_storage_is_a_boolean(name):
    assert is_boolean_type_name(name)
    assert ast.unparse(default_value_expr(_prim(name))) == "False"
    assert _prim(name).to_python() == "bool"
    assert _prim(name).type_descriptor() == "TF"


@pytest.mark.parametrize(
    "name", ["String", "String2", "CString", "PasString", "SubString"]
)
def test_every_string_storage_is_a_string(name):
    assert is_string_type_name(name)
    assert ast.unparse(default_value_expr(_prim(name))) == "''"
    assert _prim(name).to_python() == "str"
    assert _prim(name).type_descriptor() == "String"


def test_other_types_are_in_no_family():
    for name in ("NumInt32", "Path", "Cluster", None):
        assert not is_boolean_type_name(name)
        assert not is_string_type_name(name)
    assert is_integer_type_name("NumUInt8") and not is_integer_type_name("NumFloat64")
    assert plain_number_name("UnitFloat64") == "NumFloat64"
    assert plain_number_name("NumInt8") == "NumInt8"


def _body_reader(kind: REFNUM_TYPE):
    return getattr(lv_ref, f"Refnum{kind.name}", None)


def test_refnum_layout_groups_match_pylabview_readers():
    """A kind whose body is the kind alone has pylabview's base reader, which
    reads nothing; a client-list kind derives from its list reader."""
    for kind in td_pool._KIND_ONLY_REFNUMS:
        reader = _body_reader(kind)
        assert reader.parseRSRCData is lv_ref.RefnumBase.parseRSRCData, kind.name
    for kind in td_pool._CLIENT_LIST_REFNUMS:
        assert issubclass(_body_reader(kind), lv_ref.RefnumBase_SimpleCliList)


def test_reshape_array_dimension_size_is_numeric():
    template = get_resolver().resolve_by_node_type("aReshape")
    dimension = next(t for t in template.terminals if t.name == "dimension size")
    assert dimension.type == "numeric"


def _td(code: int, body: bytes, flags: int = 0x40) -> bytes:
    return struct.pack(">HBB", 4 + len(body), flags, code) + body


def _pstr(text: str) -> bytes:
    return bytes([len(text)]) + text.encode("latin1")


def test_a_data_value_reference_reads_its_external_flag_before_its_label():
    element = _td(0x03, b"", flags=0)
    dvr = _td(0x70, struct.pack(">HHH", 32, 1, 0) + b"\x00" + _pstr("dvr"))
    pool = td_pool.TdPool(
        td_pool.read_td_list(struct.pack(">I", 2) + element + dvr).tds
    )
    assert pool.failures == {}
    assert pool.names[1] == "dvr"
    assert pool.lvtypes[1].ref_type == "DataValueRef"
    assert pool.lvtypes[1].element_type is pool.lvtypes[0]


def test_a_control_reference_reads_its_label_after_its_item():
    body = (
        struct.pack(">HHHH", 8, 0, 1, 1)
        + b"ABCD"
        + struct.pack(">I", 1)
        + _pstr("Panel.ctl")
        + _pstr("ref")
    )
    pool = td_pool.TdPool(
        td_pool.read_td_list(struct.pack(">I", 1) + _td(0x70, body)).tds
    )
    assert pool.failures == {}
    assert pool.names == ["ref"]
    assert pool.lvtypes[0].ref_type == "LVObjCtl"


def test_a_refnum_kind_whose_body_is_not_read_keeps_its_kind_and_has_no_name():
    body = struct.pack(">H", REFNUM_TYPE.VisaRef) + b"\x00" * 6 + _pstr("session")
    pool = td_pool.TdPool(
        td_pool.read_td_list(struct.pack(">I", 1) + _td(0x70, body)).tds
    )
    assert pool.failures == {}
    assert pool.lvtypes[0].ref_type == "VisaRef"
    assert pool.names == [None]


def test_an_undocumented_refnum_kind_is_an_unreadable_type():
    body = struct.pack(">H", 99)
    pool = td_pool.TdPool(
        td_pool.read_td_list(struct.pack(">I", 1) + _td(0x70, body, flags=0)).tds
    )
    assert "refnum kind 99" in pool.lvtypes[0].unreadable


def test_a_type_code_that_is_not_read_is_an_unreadable_type_not_a_gap():
    pool = td_pool.TdPool(
        td_pool.read_td_list(struct.pack(">I", 1) + _td(0x37, b"", flags=0)).tds
    )
    assert "Tag" in pool.lvtypes[0].unreadable
    assert pool.failures


def test_variant_cluster_members_keep_their_labels():
    from lvkit.parser.flat_type import descriptor_type, read_descriptors

    def flat(code: int, body: bytes, flags: int) -> bytes:
        return struct.pack(">HBB", 4 + len(body), flags, code) + body

    data = (
        flat(0x03, _pstr("count"), 0x40)
        + flat(0x21, b"", 0)
        + flat(0x50, struct.pack(">HHH", 2, 0, 1), 0)
    )
    descriptors, end = read_descriptors(data, 0, 3)
    assert end == len(data)
    cluster = descriptor_type(descriptors, 2)
    assert [f.name for f in cluster.fields] == ["count", "field_1"]

"""A Call Library Function Node's prototype in a pre-LV9 VI: the node's ``funcTD``
is a heap ``TypeID`` of the ``DTHP`` pool, whose Cluster lists the rows. The pool
bytes are hand-built from the descriptors of Pico's ``usbpt104GetValue.vi``
(LabVIEW 8.5, ``UsbPt104GetValue(handle, channel, *value, filtered)``)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lvkit.parser.extfunc_pool_prototype import pool_params
from lvkit.parser.extfunc_prototype import ExtFuncParam, ParamKind
from lvkit.parser.heap_types import decode_heap_types
from lvkit.pipeline import generate_python
from tests.corpus_graphs import load_corpus_vi
from tests.test_heap_types import _dthp, _pstr, _td

I16, I32, U32, VOID, STRING = 0x02, 0x03, 0x07, 0x00, 0x30
PTR_TO, ARRAY_DATA_PTR, CLUSTER = 0x83, 0x41, 0x50


def _cluster(refs: list[int], label: str) -> bytes:
    body = struct.pack(f">H{len(refs)}H", len(refs), *refs) + _pstr(label)
    return _td(CLUSTER, body)


def _get_value_pool() -> bytes:
    tds = [
        _td(U32, _pstr("PICO_STATUS")),  # 0 return value
        _td(I16, _pstr("handle") + b"\x00"),  # 1
        _td(I32, _pstr("channel")),  # 2
        _td(I32, _pstr("value")),  # 3 what the pointer points at
        _td(PTR_TO, struct.pack(">H", 3), flags=0),  # 4 the pointer
        _td(I16, _pstr("filtered")),  # 5
        _cluster([0, 1, 2, 4, 5], "UsbPt104GetValue"),  # 6
    ]
    return _dthp(tds, [6])


def test_the_rows_of_a_pre_lv9_prototype_are_read_from_the_pool() -> None:
    assert pool_params(_get_value_pool(), 1) == (
        ExtFuncParam(ParamKind.VALUE, "uint32"),
        ExtFuncParam(ParamKind.VALUE, "int16"),
        ExtFuncParam(ParamKind.VALUE, "int32"),
        ExtFuncParam(ParamKind.POINTER, "int32"),
        ExtFuncParam(ParamKind.VALUE, "int16"),
    )


def test_heap_types_still_decode_the_same_pool() -> None:
    heap = decode_heap_types(_get_value_pool())
    assert heap.failure is None
    assert sorted(heap.types) == [1]


def test_array_data_pointer_void_and_string_rows() -> None:
    array_body = struct.pack(">HIH", 1, 0xFFFFFFFF, 1)  # one dimension, element 1
    tds = [
        _td(VOID, b"", flags=0),  # 0 return: no value
        _td(I16, _pstr("v")),  # 1
        _td(ARRAY_DATA_PTR, array_body, flags=0),  # 2 a pointer to int16 data
        _td(VOID, b"", flags=0),  # 3 a bare void: Adapt to Type
        _td(STRING, b"\x00" * 4),  # 4 a LabVIEW string handle
        _cluster([0, 2, 3, 4], "f"),
    ]
    params = pool_params(_dthp(tds, [5]), 1)
    assert [p.kind for p in params] == [
        ParamKind.NO_VALUE,
        ParamKind.ARRAY_DATA,
        ParamKind.ADAPT_TO_TYPE,
        ParamKind.STRING_HANDLE,
    ]
    assert params[1].c_type == "int16"


def test_a_type_id_that_is_not_a_cluster_has_no_prototype() -> None:
    assert pool_params(_dthp([_td(I32, _pstr("x"))], [0]), 1) == ()
    assert pool_params(_get_value_pool(), 2) == ()
    assert pool_params(b"", 1) == ()


needs_samples = pytest.mark.needs_samples


@needs_samples
def test_a_labview_85_node_has_its_prototype() -> None:
    vi = Path(
        ".lvkit/cache/samples/picosdk-ni-labview-examples/usbpt104/"
        "usbpt104Lib/usbpt104GetValue.vi"
    )
    graph, key = load_corpus_vi(vi)
    call = next(
        n.ext_func for n in graph.iter_nodes(key) if getattr(n, "ext_func", None)
    )
    assert [p.kind for p in call.params] == [
        ParamKind.VALUE,
        ParamKind.VALUE,
        ParamKind.VALUE,
        ParamKind.POINTER,
        ParamKind.VALUE,
    ]


@needs_samples
def test_a_labview_85_node_generates_ctypes_code(tmp_path: Path) -> None:
    vi = Path(
        ".lvkit/cache/samples/picosdk-ni-labview-examples/usbpt104/"
        "usbpt104Lib/usbpt104GetValue.vi"
    )
    result = generate_python(vi, tmp_path)
    source = Path(result["vi_paths"][str(vi.resolve())]).read_text()
    assert "prototype could not be read" not in source
    assert "UsbPt104GetValue" in source

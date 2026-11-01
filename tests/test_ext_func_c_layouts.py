"""LabVIEW handles and clusters passed to a C function, executed.

A small C library is compiled for the tests (when a C compiler is present). Its
functions read and write the memory they are given the way C code would, following
NI's documented layouts: a string handle points to a pointer to
``{int32 count; bytes}``; an array handle points to a pointer to the 32-bit
dimension sizes followed by the elements; a cluster is a C structure with natural
alignment, its strings and arrays handles. The generated Python is plain
``ctypes`` with the structures defined in the module."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser.extfunc_prototype import ExtFuncParam, ParamKind
from tests.ext_func_vi import build_vi, lv_type

_SOURCE = r"""
#include <stdint.h>
#include <ctype.h>

typedef struct { int32_t cnt; uint8_t str[1]; } LStr;
typedef LStr **LStrHandle;

int32_t upper(LStrHandle h) {
    LStr *s = *h;
    for (int32_t i = 0; i < s->cnt; i++) s->str[i] = (uint8_t)toupper(s->str[i]);
    return s->cnt;
}
int32_t length(LStrHandle h) { return *h ? (*h)->cnt : -1; }
void upper_through_pointer(LStrHandle *h) { if (*h) upper(*h); }
void clear_string(LStrHandle *h) { *h = 0; }
void clear_array(int32_t **h) { *h = 0; }

int32_t double_ints(int32_t **h) {
    int32_t *b = *h;
    for (int32_t i = 0; i < b[0]; i++) b[1 + i] *= 2;
    return b[0];
}
int32_t negate_matrix(int32_t **h) {
    int32_t *b = *h;
    int16_t *e = (int16_t *)(b + 2);
    for (int32_t i = 0; i < b[0] * b[1]; i++) e[i] = (int16_t)-e[i];
    return b[0] * 100 + b[1];
}

typedef struct { int8_t flag; double value; int32_t count; uint8_t ok; } Reading;
int32_t update(Reading *r) {
    r->flag -= 1; r->value *= 2; r->count += 10; r->ok = r->ok ? 0 : 1;
    return (int32_t)sizeof(Reading);
}
void swap(uint16_t *p) { uint16_t t = p[0]; p[0] = p[1]; p[1] = t; }

typedef struct {
    int16_t id;
    struct { uint8_t x; int32_t y; } inner;
    LStrHandle name;
} Outer;
void rewrite(Outer *o) { o->id += 1; o->inner.y = -o->inner.y; upper(o->name); }
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> str:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        pytest.skip("no C compiler")
    directory = tmp_path_factory.mktemp("clib")
    source = directory / "layouts.c"
    source.write_text(_SOURCE)
    target = directory / "layouts.so"
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-o", str(target), str(source)], check=True
    )
    return str(target)


_RET = ExtFuncParam(ParamKind.VALUE, "int32")
_VOID = ExtFuncParam(ParamKind.NO_VALUE)
_STRING = ExtFuncParam(ParamKind.STRING_HANDLE)


def _array(element: str, dimensions: int) -> tuple[ExtFuncParam, LVType]:
    return (
        ExtFuncParam(ParamKind.ARRAY_HANDLE, element, dimensions=dimensions),
        LVType(
            kind=LVTypeKind.ARRAY,
            element_type=lv_type({"int32": "NumInt32", "int16": "NumInt16"}[element]),
            dimensions=dimensions,
        ),
    )


def test_string_handle_is_read_and_written_in_place(library: str) -> None:
    run, source = build_vi(
        library,
        "upper",
        [_RET, _STRING],
        ["NumInt32", "String"],
        controls={1: "text"},
        indicators={0: "count", 1: "shouted"},
    )
    result = run("hello")
    assert (result.count, result.shouted) == (5, "HELLO")
    assert "lvkit" not in source
    assert (
        "class _LStr(ctypes.Structure):\n"
        "    _fields_ = [('cnt', ctypes.c_int32), ('str', ctypes.c_ubyte * 0)]"
    ) in source
    assert "function.argtypes = [ctypes.POINTER(ctypes.POINTER(_LStr))]" in source


def test_string_with_a_null_character_keeps_its_length(library: str) -> None:
    run, _ = build_vi(
        library,
        "length",
        [_RET, _STRING],
        ["NumInt32", "String"],
        controls={1: "text"},
        indicators={0: "count", 1: "text_out"},
    )
    result = run("a\x00b")
    assert (result.count, result.text_out) == (3, "a\x00b")


def test_string_handle_pointer_is_a_pointer_to_the_handle(library: str) -> None:
    run, source = build_vi(
        library,
        "upper_through_pointer",
        [_VOID, ExtFuncParam(ParamKind.STRING_HANDLE_POINTER)],
        ["NumInt32", "String"],
        controls={1: "text"},
        indicators={1: "shouted"},
    )
    assert run("abc").shouted == "ABC"
    assert (
        "function.argtypes = [ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(_LStr)))]"
    ) in source
    assert "ctypes.byref(_upper_through_pointer_1_handle)" in source


def test_a_null_string_handle_is_an_empty_string(library: str) -> None:
    run, _ = build_vi(
        library,
        "clear_string",
        [_VOID, ExtFuncParam(ParamKind.STRING_HANDLE_POINTER)],
        ["NumInt32", "String"],
        controls={1: "text"},
        indicators={1: "text_out"},
    )
    assert run("text").text_out == ""


def test_one_dimensional_array_handle(library: str) -> None:
    array, wire = _array("int32", 1)
    run, source = build_vi(
        library,
        "double_ints",
        [_RET, array],
        ["NumInt32", wire],
        controls={1: "values"},
        indicators={0: "count", 1: "doubled"},
    )
    result = run([1, 2, 3])
    assert (result.count, result.doubled) == (3, [2, 4, 6])
    assert (
        "class _Array1Int32(ctypes.Structure):\n"
        "    _fields_ = [('dim0', ctypes.c_int32), ('elt', ctypes.c_int32 * 0)]"
    ) in source


def test_two_dimensional_array_handle_has_both_sizes_before_the_data(
    library: str,
) -> None:
    array, wire = _array("int16", 2)
    run, source = build_vi(
        library,
        "negate_matrix",
        [_RET, array],
        ["NumInt32", wire],
        controls={1: "matrix"},
        indicators={0: "shape", 1: "negated"},
    )
    result = run([[1, 2, 3], [4, 5, 6]])
    assert (result.shape, result.negated) == (203, [[-1, -2, -3], [-4, -5, -6]])
    assert "('dim0', ctypes.c_int32), ('dim1', ctypes.c_int32)" in source
    assert run([]).negated == []


def test_a_null_array_handle_is_an_empty_array(library: str) -> None:
    array, wire = _array("int32", 1)
    run, _ = build_vi(
        library,
        "clear_array",
        [_VOID, array],
        ["NumInt32", wire],
        controls={1: "values"},
        indicators={1: "values_out"},
    )
    assert run([1, 2]).values_out == []


def _cluster(*fields: tuple[str, LVType | str], typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name=typedef,
        fields=[ClusterField(name=n, type=lv_type(t)) for n, t in fields],
    )


def test_cluster_is_a_structure_with_natural_alignment(library: str) -> None:
    reading = _cluster(
        ("flag", "NumInt8"),
        ("value", "NumFloat64"),
        ("count", "NumInt32"),
        ("ok", "Boolean"),
    )
    run, source = build_vi(
        library,
        "update",
        [_RET, ExtFuncParam(ParamKind.ADAPT_TO_TYPE)],
        ["NumInt32", reading],
        controls={1: "reading"},
        indicators={0: "size", 1: "updated"},
    )
    result = run((5, 1.25, 3, True))
    assert (result.size, result.updated) == (24, (4, 2.5, 13, False))
    assert (
        "class _P1Out(ctypes.Structure):\n"
        "    _fields_ = [('flag', ctypes.c_int8), ('value', ctypes.c_double), "
        "('count', ctypes.c_int32), ('ok', ctypes.c_uint8)]"
    ) in source
    assert "_update_1.flag = reading[0]" in source


def test_named_cluster_comes_back_as_a_namespace_and_reads_attributes(
    library: str,
) -> None:
    pair = _cluster(("low", "NumUInt16"), ("high", "NumUInt16"), typedef="Pair.ctl")
    run, source = build_vi(
        library,
        "swap",
        [_VOID, ExtFuncParam(ParamKind.ADAPT_TO_TYPE)],
        ["NumInt32", pair],
        controls={1: "pair"},
        indicators={1: "swapped"},
    )
    result = run(SimpleNamespace(low=1, high=2)).swapped
    assert (result.low, result.high) == (2, 1)
    assert "_swap_1.low = pair.low" in source
    assert "from types import SimpleNamespace" in source


def test_cluster_with_a_nested_cluster_and_a_string_handle(library: str) -> None:
    inner = _cluster(("x", "NumUInt8"), ("y", "NumInt32"))
    outer = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="id", type=lv_type("NumInt16")),
            ClusterField(name="inner", type=inner),
            ClusterField(name="name", type=lv_type("String")),
        ],
    )
    run, source = build_vi(
        library,
        "rewrite",
        [_VOID, ExtFuncParam(ParamKind.ADAPT_TO_TYPE)],
        ["NumInt32", outer],
        controls={1: "record"},
        indicators={1: "rewritten"},
    )
    assert run((7, (3, 40), "hi")).rewritten == (8, (3, -40), "HI")
    assert source.index("class _Inner(") < source.index("class _P1Out(")
    assert "('inner', _Inner)" in source
    assert "('name', ctypes.POINTER(ctypes.POINTER(_LStr)))" in source

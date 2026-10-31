"""Call Library Function Node code generation, executed against the C library.

Each case is a one-node VI: controls wired to the node's input sides, the node
calling a real C function, indicators wired to its output sides. The generated
Python is plain ``ctypes`` (no ``lvkit`` import) and runs; its source and results
are asserted."""

from __future__ import annotations

import ctypes.util
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes.ext_func_layout import (
    ArrayHandle,
    Cluster,
    Member,
    Scalar,
    StringHandle,
    parameter_layout,
)
from lvkit.graph import load_vi_by_path
from lvkit.load_mode import LoadMode
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser.extfunc_prototype import ExtFuncParam, ParamKind
from tests.ext_func_vi import build_vi, lv_type

_C_LIBRARY = ctypes.util.find_library("c") or (
    "msvcrt" if sys.platform == "win32" else None
)
_M_LIBRARY = ctypes.util.find_library("m") or (
    "msvcrt" if sys.platform == "win32" else None
)

_NUMBER = ExtFuncParam(ParamKind.VALUE, "int32")
_LIB = "_" + (_C_LIBRARY or "").replace(".", "_")  # the module-level library name
_MLIB = "_" + (_M_LIBRARY or "").replace(".", "_")


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_value_parameter_and_return_value() -> None:
    clfn_test, source = build_vi(
        _C_LIBRARY,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={1: "x"},
        indicators={0: "result", 1: "x_out"},
    )
    result = clfn_test(-5)
    assert (result.result, result.x_out) == (5, -5)
    assert "lvkit" not in source
    assert (
        f"{_LIB} = ctypes.CDLL({_C_LIBRARY!r})\n"
        f"{_LIB}_abs = {_LIB}['abs']\n"
        f"{_LIB}_abs.argtypes = [ctypes.c_int32]\n"
        f"{_LIB}_abs.restype = ctypes.c_int32\n"
    ) in source
    assert f"p0_out = {_LIB}_abs(x)\n    p1_out = x\n" in source


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_float_wire_into_an_integer_parameter_is_rounded() -> None:
    clfn_test, source = build_vi(
        _C_LIBRARY,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumFloat64"],
        controls={1: "x"},
        indicators={0: "result"},
    )
    assert clfn_test(-2.6).result == 3
    assert f"{_LIB}_abs(int(round(x)))" in source


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_string_parameter() -> None:
    clfn_test, source = build_vi(
        _C_LIBRARY,
        "strlen",
        [ExtFuncParam(ParamKind.VALUE, "uintptr"), ExtFuncParam(ParamKind.C_STRING)],
        ["NumUInt64", "String"],
        controls={1: "text"},
        indicators={0: "length", 1: "text_out"},
    )
    result = clfn_test("hello")
    assert (result.length, result.text_out) == (5, "hello")
    assert "_strlen_1 = ctypes.create_string_buffer(text.encode('latin-1'))" in source
    assert f"{_LIB}_strlen.argtypes = [ctypes.c_char_p]" in source
    assert "p1_out = _strlen_1.value.decode('latin-1')" in source


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_array_data_parameter_comes_back_as_the_function_left_it() -> None:
    clfn_test, source = build_vi(
        _C_LIBRARY,
        "memset",
        [
            ExtFuncParam(ParamKind.VALUE, "uintptr"),
            ExtFuncParam(ParamKind.ARRAY_DATA, "uint8"),
            _NUMBER,
            ExtFuncParam(ParamKind.VALUE, "uintptr"),
        ],
        ["NumUInt64", "Array", "NumInt32", "NumUInt64"],
        controls={1: "buffer", 2: "fill", 3: "count"},
        indicators={1: "filled"},
    )
    assert clfn_test([0, 0, 0, 0, 9], 65, 4).filled == [65, 65, 65, 65, 9]
    assert "_memset_1 = (ctypes.c_uint8 * len(buffer))(*buffer)" in source
    assert "p1_out = list(_memset_1)" in source


@pytest.mark.skipif(_M_LIBRARY is None, reason="no math library to call")
def test_pointer_parameter_output_side_is_the_value_the_function_wrote() -> None:
    clfn_test, source = build_vi(
        _M_LIBRARY,
        "frexp",
        [
            ExtFuncParam(ParamKind.VALUE, "float64"),
            ExtFuncParam(ParamKind.VALUE, "float64"),
            ExtFuncParam(ParamKind.POINTER, "int32"),
        ],
        ["NumFloat64", "NumFloat64", "NumInt32"],
        controls={1: "x"},
        indicators={0: "mantissa", 2: "exponent"},
    )
    result = clfn_test(8.0)
    assert (result.mantissa, result.exponent) == (0.5, 4)
    assert "_frexp_2 = ctypes.c_int32(0)" in source
    assert "ctypes.byref(_frexp_2)" in source
    assert "p2_out = _frexp_2.value" in source


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_unwired_inputs_are_the_type_default() -> None:
    clfn_test, source = build_vi(
        _C_LIBRARY,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={},
        indicators={0: "result"},
    )
    assert f"{_LIB}_abs(0)" in source
    assert clfn_test().result == 0


def test_a_library_that_cannot_be_loaded_raises_os_error_on_import() -> None:
    with pytest.raises(OSError):
        build_vi(
            "no_such_library_for_lvkit",
            "f",
            [_NUMBER],
            ["NumInt32"],
            controls={},
            indicators={0: "result"},
        )


_PLUGIN_ROOT = Path(".lvkit/cache/samples/measurement-plugin-labview")


@pytest.mark.needs_samples
@pytest.mark.parametrize(
    "vi_path,function",
    [
        pytest.param(
            _PLUGIN_ROOT
            / "Source/Tests/Tests.Runtime/Measurement Server/Helpers"
            / "Write Environment Variable.vi",
            "SetEnvironmentVariableA",
            id="Write_Environment_Variable",
        ),
        pytest.param(
            _PLUGIN_ROOT
            / "Source/Runtime/Clients/Session Management V1/Session Reservation"
            / "Get Client Machine Details.vi",
            "GetComputerNameA",
            id="Get_Client_Machine_Details",
        ),
    ],
)
def test_corpus_vi_generates_python_that_compiles(vi_path: Path, function: str) -> None:
    if not vi_path.exists():
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = load_vi_by_path(vi_path, LoadMode.FULL)
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    compile(source, vi_path.name, "exec")
    assert (
        "_kernel32_dll = getattr(ctypes, 'WinDLL', ctypes.CDLL)('kernel32.dll')"
        in source
    )
    assert f"_kernel32_dll[{function!r}]" in source


_TIMESPEC = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField(name="seconds", type=lv_type("NumInt64")),
        ClusterField(name="nanoseconds", type=lv_type("NumInt64")),
    ],
)


@pytest.mark.skipif(
    _C_LIBRARY is None or sys.platform == "win32", reason="needs clock_gettime"
)
def test_adapt_to_type_cluster_is_a_pointer_to_a_structure_of_its_fields() -> None:
    """clock_gettime(clockid, struct timespec *) fills the cluster {int64 seconds;
    int64 nanoseconds} the node's Adapt to Type parameter is wired to."""
    clfn_test, source = build_vi(
        _C_LIBRARY,
        "clock_gettime",
        [_NUMBER, _NUMBER, ExtFuncParam(ParamKind.ADAPT_TO_TYPE)],
        ["NumInt32", "NumInt32", _TIMESPEC],
        controls={1: "clock"},
        indicators={0: "status", 2: "now"},
    )
    result = clfn_test(0)  # CLOCK_REALTIME
    assert result.status == 0
    seconds, nanoseconds = result.now
    assert seconds > 1_600_000_000 and 0 <= nanoseconds < 1_000_000_000
    assert (
        "class _P2Out(ctypes.Structure):\n"
        "    _fields_ = [('seconds', ctypes.c_int64), ('nanoseconds', ctypes.c_int64)]"
    ) in source
    assert "_clock_gettime_2 = _P2Out()" in source
    assert "p2_out = (_clock_gettime_2.seconds, _clock_gettime_2.nanoseconds)" in source
    assert (
        f"{_LIB}_clock_gettime.argtypes = [ctypes.c_int32, ctypes.POINTER(_P2Out)]"
        in source
    )


def test_parameter_layout_of_every_wire_type() -> None:
    string = lv_type("String")
    boolean = lv_type("Boolean")
    numbers = LVType(
        kind=LVTypeKind.ARRAY, element_type=lv_type("NumInt16"), dimensions=2
    )
    nested = LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Point.ctl",
        fields=[
            ClusterField(name="x", type=lv_type("NumFloat32")),
            ClusterField(name="label", type=string),
            ClusterField(name="on", type=boolean),
            ClusterField(name="samples", type=numbers),
        ],
    )
    outer = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="count", type=lv_type("NumUInt32")),
            ClusterField(name="point", type=nested),
        ],
    )
    assert parameter_layout(lv_type("NumUInt64"), "n") == (Scalar("uint64"), "")
    assert parameter_layout(boolean, "b") == (Scalar("bool"), "")
    layout, why = parameter_layout(outer, "request")
    assert why == ""
    point = Cluster(
        nested.python_class_name() or "",
        True,
        (
            Member("x", Scalar("float32")),
            Member("label", StringHandle()),
            Member("on", Scalar("bool")),
            Member("samples", ArrayHandle("int16", 2)),
        ),
    )
    assert layout == Cluster(
        "Request", False, (Member("count", Scalar("uint32")), Member("point", point))
    )
    # A top-level string or array depends on a Data Format setting that is not
    # decoded; a path is opaque; an array of doubles has no settled data offset.
    for wire in (string, numbers, lv_type("Path")):
        none, why = parameter_layout(wire, "x")
        assert none is None and "Data Format" in why
    holder = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(
                name="data",
                type=LVType(
                    kind=LVTypeKind.ARRAY,
                    element_type=lv_type("NumFloat64"),
                    dimensions=1,
                ),
            )
        ],
    )
    none, why = parameter_layout(holder, "x")
    assert none is None and "padding" in why


def test_labview_library_function_the_table_lacks_raises_where_it_runs() -> None:
    _, source = build_vi(
        "LabVIEW",
        "FNoSuchFunction",
        [_NUMBER],
        ["NumInt32"],
        controls={},
        indicators={0: "error"},
    )
    assert ("LabVIEW's function FNoSuchFunction has no Python equivalent") in source
    assert "raise NotImplementedError(" in source

"""Call Library Function Node code generation, executed against the C library.

Each case is a one-node VI: controls wired to the node's input sides, the node
calling a real C function, indicators wired to its output sides. The generated
Python runs and its results are asserted."""

from __future__ import annotations

import ctypes.util
import sys
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.load_mode import LoadMode
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser.extfunc_prototype import ExtFuncCall, ExtFuncParam, ParamKind
from lvkit.primitive_resolver import PrimitiveResolutionNeeded
from tests.helpers import build_graph

_C_LIBRARY = ctypes.util.find_library("c") or (
    "msvcrt" if sys.platform == "win32" else None
)
_M_LIBRARY = ctypes.util.find_library("m") or (
    "msvcrt" if sys.platform == "win32" else None
)


def _type(name: str | LVType) -> LVType:
    if isinstance(name, LVType):
        return name
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _vi(
    library: str | None,
    function: str,
    params: list[ExtFuncParam],
    wire_types: list[str | LVType],
    controls: dict[int, str],
    indicators: dict[int, str],
) -> tuple[Any, str]:
    """Generate and load a VI around one Call Library Function Node.

    ``params``/``wire_types`` are the prototype rows (return value first).
    ``controls`` maps a row to the control name wired to its input side;
    ``indicators`` maps a row to the indicator wired to its output side."""
    rows = [
        (None if row == 0 else f"n:in{row}", f"n:out{row}")
        for row in range(len(params))
    ]
    terminals: list[Terminal] = []
    for row, (input_id, output_id) in enumerate(rows):
        if input_id:
            terminals.append(
                Terminal(
                    id=input_id,
                    index=2 * row,
                    direction="input",
                    name=f"p{row}",
                    lv_type=_type(wire_types[row]),
                )
            )
        terminals.append(
            Terminal(
                id=output_id,
                index=2 * row + 1,
                direction="output",
                name=f"p{row}_out",
                lv_type=_type(wire_types[row]),
            )
        )
    node = PrimitiveNode(
        id="n",
        vi_path="Clfn Test.vi",
        name=f"{library}:{function}",
        node_type="extFunc",
        terminals=terminals,
        ext_func=ExtFuncCall(
            library=library,
            function=function,
            calling_convention="c",
            path_in_terminal_id=None,
            row_terminal_ids=tuple(rows),
            params=tuple(params),
        ),
    )
    inputs = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="input",
            name=name,
            is_public=True,
            is_indicator=False,
            lv_type=_type(wire_types[row]),
        )
        for i, (row, name) in enumerate(controls.items())
    ]
    outputs = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="output",
            name=name,
            is_public=True,
            is_indicator=True,
            lv_type=_type(wire_types[row]),
        )
        for i, (row, name) in enumerate(indicators.items())
    ]
    wires = [
        Wire.from_terminals(from_terminal_id=f"fp:{name}", to_terminal_id=f"n:in{row}")
        for row, name in controls.items()
    ] + [
        Wire.from_terminals(from_terminal_id=f"n:out{row}", to_terminal_id=f"fp:{name}")
        for row, name in indicators.items()
    ]
    context = VIContext(
        name="Clfn Test.vi", inputs=inputs, outputs=outputs, data_flow=wires
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace: dict[str, Any] = {}
    exec(compile(source, context.name, "exec"), namespace)  # noqa: S102
    return namespace["clfn_test"], source


_NUMBER = ExtFuncParam(ParamKind.VALUE, "int32")


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_value_parameter_and_return_value() -> None:
    clfn_test, _ = _vi(
        _C_LIBRARY,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={1: "x"},
        indicators={0: "result", 1: "x_out"},
    )
    result = clfn_test(-5)
    assert (result.result, result.x_out) == (5, -5)


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_string_parameter() -> None:
    clfn_test, _ = _vi(
        _C_LIBRARY,
        "strlen",
        [ExtFuncParam(ParamKind.VALUE, "uintptr"), ExtFuncParam(ParamKind.C_STRING)],
        ["NumUInt64", "String"],
        controls={1: "text"},
        indicators={0: "length", 1: "text_out"},
    )
    result = clfn_test("hello")
    assert (result.length, result.text_out) == (5, "hello")


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_array_data_parameter_comes_back_as_the_function_left_it() -> None:
    clfn_test, _ = _vi(
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


@pytest.mark.skipif(_M_LIBRARY is None, reason="no math library to call")
def test_pointer_parameter_output_side_is_the_value_the_function_wrote() -> None:
    clfn_test, _ = _vi(
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


@pytest.mark.skipif(_C_LIBRARY is None, reason="no C library to call")
def test_unwired_inputs_are_the_type_default() -> None:
    clfn_test, source = _vi(
        _C_LIBRARY,
        "abs",
        [_NUMBER, _NUMBER],
        ["NumInt32", "NumInt32"],
        controls={},
        indicators={0: "result"},
    )
    assert "[0])" in source
    assert clfn_test().result == 0


def test_a_missing_library_is_labview_error_7() -> None:
    from lvkit.labview_error import LabVIEWError

    clfn_test, _ = _vi(
        "no_such_library_for_lvkit",
        "f",
        [_NUMBER],
        ["NumInt32"],
        controls={},
        indicators={0: "result"},
    )
    with pytest.raises(LabVIEWError) as raised:
        clfn_test()
    assert raised.value.code == 7


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
    graph, vi_name = load_vi_by_path(
        vi_path, LoadMode.FULL, search_paths=[_PLUGIN_ROOT]
    )
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    compile(source, vi_path.name, "exec")
    assert f"'{function}', 'stdcall'" in source


_TIMESPEC = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField(name="seconds", type=_type("NumInt64")),
        ClusterField(name="nanoseconds", type=_type("NumInt64")),
    ],
)


@pytest.mark.skipif(
    _C_LIBRARY is None or sys.platform == "win32", reason="needs clock_gettime"
)
def test_adapt_to_type_cluster_is_a_pointer_to_a_structure_of_its_fields() -> None:
    """clock_gettime(clockid, struct timespec *) fills the cluster {int64 seconds;
    int64 nanoseconds} the node's Adapt to Type parameter is wired to."""
    clfn_test, source = _vi(
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
        "('cluster', False, (('seconds', 'int64'), ('nanoseconds', 'int64')))" in source
    )


def test_adapt_spec_of_every_wire_type() -> None:
    from lvkit.codegen.nodes.ext_func_adapt import adapt_spec

    string = _type("String")
    boolean = _type("Boolean")
    numbers = LVType(
        kind=LVTypeKind.ARRAY, element_type=_type("NumInt16"), dimensions=2
    )
    nested = LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name="Point.ctl",
        fields=[
            ClusterField(name="x", type=_type("NumFloat32")),
            ClusterField(name="label", type=string),
            ClusterField(name="on", type=boolean),
            ClusterField(name="samples", type=numbers),
        ],
    )
    outer = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="count", type=_type("NumUInt32")),
            ClusterField(name="point", type=nested),
        ],
    )
    assert adapt_spec(_type("NumUInt64")) == ("uint64", "")
    assert adapt_spec(boolean) == ("bool", "")
    assert adapt_spec(outer) == (
        (
            "cluster",
            False,
            (
                ("count", "uint32"),
                (
                    "point",
                    (
                        "cluster",
                        True,
                        (
                            ("x", "float32"),
                            ("label", ("string",)),
                            ("on", "bool"),
                            ("samples", ("array", "int16", 2)),
                        ),
                    ),
                ),
            ),
        ),
        "",
    )
    # A top-level string or array depends on a Data Format setting that is not
    # decoded; a path is opaque; an array of doubles has no settled data offset.
    for lv_type in (string, numbers, _type("Path")):
        spec, why = adapt_spec(lv_type)
        assert spec is None and "Data Format" in why
    holder = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(
                name="data",
                type=LVType(
                    kind=LVTypeKind.ARRAY,
                    element_type=_type("NumFloat64"),
                    dimensions=1,
                ),
            )
        ],
    )
    spec, why = adapt_spec(holder)
    assert spec is None and "padding" in why


def test_labview_library_function_runs_its_python_equivalent(tmp_path: Path) -> None:
    """A node that names the library LabVIEW lowers to the Python the table lists
    for the function: FGetInfo fills the 552-byte record of a file."""
    clfn_test, source = _vi(
        "LabVIEW",
        "FGetInfo",
        [
            _NUMBER,
            ExtFuncParam(ParamKind.ADAPT_TO_TYPE),
            ExtFuncParam(ParamKind.ARRAY_DATA, "uint8"),
        ],
        ["NumInt32", "Path", "Array"],
        controls={1: "file", 2: "record"},
        indicators={0: "error", 2: "info"},
    )
    assert "_lv_manager.get_info(file, record)" in source
    assert "_clfn" not in source
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    result = clfn_test(str(target), [0] * 552)
    assert result.error == 0
    assert int.from_bytes(bytes(result.info[12:16]), sys.byteorder, signed=True) == 3
    assert clfn_test(str(tmp_path / "nope"), [0] * 552).error == 7


def test_labview_library_function_the_table_lacks_fails_loudly() -> None:
    with pytest.raises(PrimitiveResolutionNeeded) as raised:
        _vi(
            "LabVIEW",
            "FNoSuchFunction",
            [_NUMBER],
            ["NumInt32"],
            controls={},
            indicators={0: "error"},
        )
    assert "LabVIEW's function FNoSuchFunction has no Python equivalent" in str(
        raised.value
    )


_OPENG = Path(".lvkit/cache/samples/OpenG/extracted")
_OPENG_LIBRARIES = _OPENG / "File Group 0/user.lib/_OpenG.lib"


@pytest.mark.needs_samples
@pytest.mark.parametrize(
    "vi_path,call",
    [
        pytest.param(
            _OPENG_LIBRARIES
            / "largefile/largefile.llb/LFU Create File Handle__ogtk.vi",
            "_lv_manager.new_ref_num(",
            id="FNewRefNum",
        ),
        pytest.param(
            _OPENG_LIBRARIES
            / "largefile/largefile.llb/LFU Retrieve File Handle__ogtk.vi",
            "_lv_manager.ref_num_to_fd(",
            id="FRefNumToFD",
        ),
        pytest.param(
            _OPENG_LIBRARIES / "file/file.llb/File Info__ogtk.vi",
            "_lv_manager.get_info(",
            id="FGetInfo_FSetInfo",
        ),
    ],
)
def test_corpus_vi_calling_labview_functions_generates_python_that_compiles(
    vi_path: Path, call: str
) -> None:
    if not vi_path.exists():
        pytest.skip(f"sample corpus VI not present: {vi_path}")
    graph, vi_name = load_vi_by_path(vi_path, LoadMode.FULL, search_paths=[_OPENG])
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    compile(source, vi_path.name, "exec")
    assert call in source

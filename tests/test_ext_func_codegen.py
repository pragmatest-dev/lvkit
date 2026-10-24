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
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser.extfunc_prototype import ExtFuncCall, ExtFuncParam, ParamKind
from tests.helpers import build_graph

_C_LIBRARY = ctypes.util.find_library("c") or (
    "msvcrt" if sys.platform == "win32" else None
)
_M_LIBRARY = ctypes.util.find_library("m") or (
    "msvcrt" if sys.platform == "win32" else None
)


def _type(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _vi(
    library: str | None,
    function: str,
    params: list[ExtFuncParam],
    wire_types: list[str],
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
